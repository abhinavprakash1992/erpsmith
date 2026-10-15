import json

import frappe
from frappe.tests.utils import FrappeTestCase

from erpsmith import guards, mcp

RESTRICTED = "mcp-restricted@example.com"


def call(name, args):
	r = mcp.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
		"params": {"name": name, "arguments": args}})["result"]
	return r.get("isError", False), json.loads(r["content"][0]["text"])


def approved(tool, args):
	guards.approve_plan(guards.submit_plan("test", [{"tool": tool, "args": args}])["planId"])


class TestRecords(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("User", RESTRICTED):
			frappe.get_doc({"doctype": "User", "email": RESTRICTED, "first_name": "MCP Restricted",
				"send_welcome_email": 0}).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")

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
