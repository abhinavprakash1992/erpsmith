import frappe
from frappe.tests.utils import FrappeTestCase

from erpsmith.tests.helpers import approved, call, run

RESTRICTED = "mcp-restricted@example.com"
VICTIM = "mcp-victim@example.com"


class TestRecords(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		for email in (RESTRICTED, VICTIM):
			if not frappe.db.exists("User", email):
				frappe.get_doc({"doctype": "User", "email": email, "first_name": "MCP Test",
					"send_welcome_email": 0}).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")

	def subscribe(self, user, document):
		"""Give `user` one Notification Settings row (a child row only that user may change); returns its name."""
		settings = frappe.get_doc("Notification Settings", user)
		settings.set("subscribed_documents", [{"document": document}])
		settings.save(ignore_permissions=True)
		return settings.subscribed_documents[0].name

	def row(self, name):
		return frappe.db.get_value("Notification Subscribed Document", name, ["parent", "document"])

	def test_crud_round_trip(self):
		create = {"doctype": "ToDo", "data": {"description": "mcp round trip"}}
		approved("create_record", create)
		err, out = call("create_record", create)
		self.assertFalse(err, out)
		name = out["name"]

		err, got = call("get_record", {"doctype": "ToDo", "name": name})
		self.assertFalse(err, got)
		self.assertEqual(got["data"]["description"], "mcp round trip")

		update = {"doctype": "ToDo", "name": name, "data": {"description": "changed"}}
		approved("update_record", update)
		err, out = call("update_record", update)
		self.assertFalse(err, out)
		self.assertEqual(out["data"]["description"], "changed")

		err, q = call("query_records", {"doctype": "ToDo", "filters": [["name", "=", name]], "fields": ["name", "description"]})
		self.assertFalse(err, q)
		self.assertEqual(q["count"], 1)
		self.assertEqual(q["data"][0]["description"], "changed")

		delete = {"doctype": "ToDo", "name": name}
		approved("delete_record", delete)
		err, out = call("delete_record", delete)
		self.assertFalse(err, out)
		self.assertFalse(frappe.db.exists("ToDo", name))

	def test_writes_without_a_plan_are_blocked(self):
		err, out = call("create_record", {"doctype": "ToDo", "data": {"description": "no plan here"}})
		self.assertTrue(err)
		self.assertTrue(out["message"].startswith("BLOCKED by plan gate"))

	def test_query_limit_is_capped_at_200(self):
		err, q = call("query_records", {"doctype": "DocType", "limit": 5000})
		self.assertFalse(err, q)
		self.assertEqual(q["count"], min(frappe.db.count("DocType"), 200))

	def test_query_limit_zero_or_negative_cannot_lift_the_cap(self):
		for limit, expected in ((0, 50), (None, 50), (-1, 1)):
			err, q = call("query_records", {"doctype": "DocType", "limit": limit})
			self.assertFalse(err, q)
			self.assertEqual(q["count"], expected, limit)

	def test_restricted_user_cannot_read_what_desk_hides(self):
		log = frappe.log_error("mcp permission probe")
		self.addCleanup(frappe.db.delete, "Error Log", {"name": log.name})  # MyISAM: a rollback would not remove it
		frappe.set_user(RESTRICTED)
		err, out = call("get_record", {"doctype": "Error Log", "name": log.name})
		self.assertTrue(err)
		self.assertEqual(out["type"], "PermissionError")
		err, out = call("query_records", {"doctype": "Error Log"})
		self.assertTrue(err)
		self.assertEqual(out["type"], "PermissionError")

	def test_flags_cannot_smuggle_ignore_permissions(self):
		frappe.set_user(RESTRICTED)
		args = {"doctype": "Role", "data": {"role_name": "MCP Smuggled", "flags": {"ignore_permissions": True}}}
		approved("create_record", args)
		err, out = call("create_record", args)
		self.assertTrue(err)
		self.assertEqual(out["type"], "PermissionError")
		frappe.set_user("Administrator")
		self.assertFalse(frappe.db.exists("Role", "MCP Smuggled"))

	def test_restricted_user_cannot_update_or_delete_what_desk_forbids(self):
		role = frappe.get_doc({"doctype": "Role", "role_name": "MCP Probe Role"}).insert()
		gender = frappe.get_doc({"doctype": "Gender", "gender": "MCP Probe Gender"}).insert()
		frappe.set_user(RESTRICTED)
		update = {"doctype": "Role", "name": role.name, "data": {"disabled": 1, "flags": {"ignore_permissions": True}}}
		delete = {"doctype": "Gender", "name": gender.name}
		for tool, args in (("update_record", update), ("delete_record", delete)):
			approved(tool, args)
			err, out = call(tool, args)
			self.assertTrue(err)
			self.assertEqual(out["type"], "PermissionError", out)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Role", role.name, "disabled"), 0)
		self.assertTrue(frappe.db.exists("Gender", gender.name))

	def test_update_cannot_forge_the_fields_a_permission_hook_reads(self):
		# ToDo lets a user write only their own: the hook reads allocated_to from the document.
		todo = frappe.get_doc({"doctype": "ToDo", "description": "admin task", "allocated_to": "Administrator"}).insert()
		frappe.set_user(RESTRICTED)
		args = {"doctype": "ToDo", "name": todo.name, "data": {"description": "taken over", "allocated_to": RESTRICTED}}
		approved("update_record", args)
		err, out = call("update_record", args)
		self.assertTrue(err)
		self.assertEqual(out["type"], "PermissionError", out)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("ToDo", todo.name, ["description", "allocated_to"]), ("admin task", "Administrator"))

	def test_update_cannot_be_retargeted_at_another_record(self):
		frappe.set_user(RESTRICTED)
		mine = frappe.get_doc({"doctype": "ToDo", "description": "mine", "allocated_to": RESTRICTED}).insert()
		frappe.set_user("Administrator")
		theirs = frappe.get_doc({"doctype": "ToDo", "description": "theirs", "allocated_to": "Administrator"}).insert()
		forged = {"name": theirs.name, "modified": theirs.modified, "creation": theirs.creation, "owner": theirs.owner,
			"allocated_to": RESTRICTED, "description": "retargeted"}
		frappe.set_user(RESTRICTED)
		args = {"doctype": "ToDo", "name": mine.name, "data": forged}
		approved("update_record", args)
		call("update_record", args)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("ToDo", theirs.name, "description"), "theirs")

	def test_update_cannot_move_a_child_row_from_another_document(self):
		victim_row = self.subscribe(VICTIM, "ToDo")
		self.subscribe(RESTRICTED, "Note")
		frappe.set_user(RESTRICTED)
		args = {"doctype": "Notification Settings", "name": RESTRICTED,
			"data": {"subscribed_documents": [{"name": victim_row, "document": "Event"}]}}
		approved("update_record", args)
		err, out = call("update_record", args)
		self.assertFalse(err, out)
		frappe.set_user("Administrator")
		self.assertEqual(self.row(victim_row), (VICTIM, "ToDo"))
		(mine,) = frappe.get_all("Notification Subscribed Document", filters={"parent": RESTRICTED}, pluck="name")
		self.assertEqual(self.row(mine), (RESTRICTED, "Event"))

		# the caller's own row keeps its name, so it is updated in place
		frappe.set_user(RESTRICTED)
		args = {"doctype": "Notification Settings", "name": RESTRICTED,
			"data": {"subscribed_documents": [{"name": mine, "document": "Note"}]}}
		approved("update_record", args)
		err, out = call("update_record", args)
		self.assertFalse(err, out)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.get_all("Notification Subscribed Document", filters={"parent": RESTRICTED}, pluck="name"), [mine])
		self.assertEqual(self.row(mine), (RESTRICTED, "Note"))

	def test_create_cannot_take_over_a_child_row_of_another_document(self):
		victim_row = self.subscribe(VICTIM, "ToDo")
		frappe.set_user(RESTRICTED)
		args = {"doctype": "Notification Subscribed Document", "data": {"parenttype": "Notification Settings",
			"parent": RESTRICTED, "parentfield": "subscribed_documents", "name": victim_row, "document": "Note"}}
		approved("create_record", args)
		err, out = call("create_record", args)
		self.assertFalse(err, out)
		frappe.set_user("Administrator")
		self.assertEqual(self.row(victim_row), (VICTIM, "ToDo"))
		self.assertIn("Note", frappe.get_all("Notification Subscribed Document", filters={"parent": RESTRICTED}, pluck="document"))

	def test_create_ignores_names_on_nested_rows(self):
		victim_group = frappe.get_doc({"doctype": "User Group", "name": "MCP Victim Group",
			"user_group_members": [{"user": VICTIM}]}).insert()
		victim_row = victim_group.user_group_members[0].name
		args = {"doctype": "User Group", "data": {"name": "MCP New Group",
			"user_group_members": [{"name": victim_row, "user": RESTRICTED}]}}
		approved("create_record", args)
		err, out = call("create_record", args)
		self.assertFalse(err, out)
		self.assertEqual(frappe.db.get_value("User Group Member", victim_row, ["parent", "user"]), (victim_group.name, VICTIM))
		self.assertEqual(frappe.get_all("User Group Member", filters={"parent": "MCP New Group"}, pluck="user"), [RESTRICTED])

	def test_updating_a_child_row_saves_its_parent_too(self):
		row = self.subscribe(RESTRICTED, "Note")
		before = frappe.db.get_value("Notification Settings", RESTRICTED, "modified")
		frappe.set_user(RESTRICTED)
		args = {"doctype": "Notification Subscribed Document", "name": row, "data": {"document": "Event"}}
		approved("update_record", args)
		err, out = call("update_record", args)
		self.assertFalse(err, out)
		frappe.set_user("Administrator")
		self.assertEqual(self.row(row), (RESTRICTED, "Event"))
		self.assertGreater(frappe.db.get_value("Notification Settings", RESTRICTED, "modified"), before)

	def test_owner_and_creation_cannot_be_forged(self):
		err, out = run("create_record", {"doctype": "ToDo", "data": {"description": "forged", "owner": VICTIM,
			"creation": "2001-01-01 00:00:00"}})
		self.assertFalse(err, out)
		row = frappe.db.get_value("ToDo", out["name"], ["owner", "creation"], as_dict=True)
		self.assertEqual(row.owner, "Administrator")
		self.assertNotEqual(str(row.creation)[:4], "2001")

		err, out = run("update_record", {"doctype": "ToDo", "name": out["name"], "data": {"owner": VICTIM,
			"creation": "2001-01-01 00:00:00", "description": "still mine"}})
		self.assertFalse(err, out)
		row = frappe.db.get_value("ToDo", out["updated"], ["owner", "creation", "description"], as_dict=True)
		self.assertEqual((row.owner, row.description), ("Administrator", "still mine"))
		self.assertNotEqual(str(row.creation)[:4], "2001")
