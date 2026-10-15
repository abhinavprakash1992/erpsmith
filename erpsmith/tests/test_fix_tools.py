import frappe
from frappe.tests.utils import FrappeTestCase

from erpsmith.tests.helpers import drop_doctype, forget_plans, run

GADGET = "ERPSmith Test Gadget"
WORKFLOW = "ERPSmith Test Gadget Flow"
ROLE = "ERPSmith Test Role"


class TestFixTools(FrappeTestCase):
	"""One scratch DocType with a workflow for the whole class (they commit); each test runs inside a savepoint."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.get_doc({"doctype": "DocType", "name": GADGET, "module": "Custom", "custom": 1, "autoname": "hash",
			"fields": [{"fieldname": "title", "label": "Title", "fieldtype": "Data", "reqd": 1},
				{"fieldname": "related_note", "label": "Related Note", "fieldtype": "Link", "options": "Note"}],
			"permissions": [{"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1}]}).insert()
		frappe.get_doc({"doctype": "Workflow", "workflow_name": WORKFLOW, "document_type": GADGET, "workflow_state_field": "workflow_state",
			"is_active": 1, "states": [{"state": "Pending", "doc_status": "0", "allow_edit": "System Manager"},
				{"state": "Approved", "doc_status": "0", "allow_edit": "System Manager"}],
			"transitions": [{"state": "Pending", "action": "Approve", "next_state": "Approved", "allowed": "System Manager"}]}).insert()
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		super().tearDownClass()
		if frappe.db.exists("Workflow", WORKFLOW):
			frappe.delete_doc("Workflow", WORKFLOW, force=True)
		drop_doctype(GADGET)
		forget_plans()

	def setUp(self):
		frappe.db.savepoint("fix_test")

	def tearDown(self):
		frappe.db.rollback(save_point="fix_test")
		frappe.clear_cache(doctype=GADGET)

	def gadget(self, **values):
		return frappe.get_doc({"doctype": GADGET, "title": "t", **values}).insert()

	def note(self):
		return frappe.get_doc({"doctype": "Note", "title": "ERPSmith Test Note"}).insert().name

	def test_fix_stuck_document_moves_it_and_leaves_a_comment(self):
		doc = self.gadget()
		doc.db_set("workflow_state", "ERPSmith Gone")
		err, out = run("fix_stuck_document", {"doctype": GADGET, "docName": doc.name, "targetState": "Approved"})
		self.assertFalse(err, out)
		self.assertEqual((out["previousState"], out["newState"]), ("ERPSmith Gone", "Approved"))
		self.assertEqual(frappe.db.get_value(GADGET, doc.name, "workflow_state"), "Approved")
		self.assertTrue(frappe.db.exists("Comment", {"reference_doctype": GADGET, "reference_name": doc.name,
			"content": ("like", "%fix_stuck_document%")}))

		err, out = run("fix_stuck_document", {"doctype": GADGET, "docName": doc.name, "targetState": "ERPSmith Nowhere"})
		self.assertEqual(out["type"], "InvalidArguments")
		doc.db_set("workflow_state", "ERPSmith Gone")
		frappe.db.sql("update `tabWorkflow Document State` set doc_status='1' where parent=%s and state='Approved'", WORKFLOW)
		err, out = run("fix_stuck_document", {"doctype": GADGET, "docName": doc.name, "targetState": "Approved"})
		self.assertEqual(out["type"], "InvalidArguments")
		self.assertIn("docstatus", out["message"])

	def test_fix_stuck_document_refuses_a_document_in_a_valid_state(self):
		doc = self.gadget()
		self.assertEqual(doc.workflow_state, "Pending")
		err, out = run("fix_stuck_document", {"doctype": GADGET, "docName": doc.name, "targetState": "Approved"})
		self.assertEqual(out["type"], "InvalidArguments", out)
		self.assertIn("not stuck", out["message"])
		self.assertEqual(frappe.db.get_value(GADGET, doc.name, "workflow_state"), "Pending")

	def test_activate_workflow(self):
		frappe.db.set_value("Workflow", WORKFLOW, "is_active", 0)
		err, out = run("activate_workflow", {"workflowName": WORKFLOW})
		self.assertFalse(err, out)
		self.assertEqual((out["activated"], out["deactivated"]), (WORKFLOW, []))
		self.assertEqual(frappe.db.get_value("Workflow", WORKFLOW, "is_active"), 1)

	def test_fix_permission_keeps_the_existing_rules(self):
		frappe.get_doc({"doctype": "Role", "role_name": ROLE}).insert()
		err, out = run("fix_permission", {"doctype": GADGET, "role": ROLE, "permissions": {"read": 1, "write": 1}})
		self.assertFalse(err, out)
		rules = {r.role: (r.read, r.write) for r in frappe.get_all("Custom DocPerm", filters={"parent": GADGET}, fields=["role", "read", "write"])}
		self.assertEqual(rules, {"System Manager": (1, 1), ROLE: (1, 1)})

	def test_fix_broken_link_value(self):
		gone, replacement = self.note(), self.note()
		doc = self.gadget(related_note=gone)
		frappe.db.delete("Note", {"name": gone})
		err, out = run("fix_broken_link_value", {"doctype": GADGET, "recordName": doc.name, "fieldname": "related_note",
			"correctValue": replacement})
		self.assertFalse(err, out)
		self.assertEqual(frappe.db.get_value(GADGET, doc.name, "related_note"), replacement)
		for fieldname, value in (("related_note", "ERPSmith No Such Note"), ("title", replacement)):
			err, out = run("fix_broken_link_value", {"doctype": GADGET, "recordName": doc.name, "fieldname": fieldname, "correctValue": value})
			self.assertEqual(out["type"], "InvalidArguments", out)
		err, out = run("fix_broken_link_value", {"doctype": GADGET, "recordName": doc.name, "fieldname": "related_note", "correctValue": ""})
		self.assertFalse(err, out)
		self.assertIsNone(frappe.db.get_value(GADGET, doc.name, "related_note"))

	def test_fix_missing_required_value(self):
		doc = self.gadget()
		frappe.db.set_value(GADGET, doc.name, "title", "")
		err, out = run("fix_missing_required_value", {"doctype": GADGET, "recordName": doc.name, "fieldname": "title", "value": "restored"})
		self.assertFalse(err, out)
		self.assertEqual(frappe.db.get_value(GADGET, doc.name, "title"), "restored")
		for fieldname, value in (("title", ""), ("no_such_field", "x")):
			err, out = run("fix_missing_required_value", {"doctype": GADGET, "recordName": doc.name, "fieldname": fieldname, "value": value})
			self.assertEqual(out["type"], "InvalidArguments", out)


class TestFixBrokenLink(FrappeTestCase):
	"""Saving a custom DocType commits, so this test makes and drops its own DocType instead of using a savepoint."""

	def setUp(self):
		frappe.get_doc({"doctype": "DocType", "name": GADGET, "module": "Custom", "custom": 1, "autoname": "hash",
			"fields": [{"fieldname": "related_note", "label": "Related Note", "fieldtype": "Link", "options": "Note"}],
			"permissions": [{"role": "System Manager", "read": 1, "write": 1, "create": 1}]}).insert()
		frappe.db.sql("update `tabDocField` set options='ERPSmith Test Missing' where parent=%s and fieldname='related_note'", GADGET)
		frappe.clear_cache(doctype=GADGET)

	def tearDown(self):
		drop_doctype(GADGET)
		forget_plans()

	def test_fix_broken_link_only_changes_link_and_table_fields(self):
		err, out = run("fix_broken_link", {"doctype": "ToDo", "fieldname": "description", "correctDoctype": "Note"})
		self.assertEqual(out["type"], "InvalidArguments", out)

	def test_fix_broken_link_points_the_field_at_an_existing_doctype(self):
		err, out = run("fix_broken_link", {"doctype": GADGET, "fieldname": "related_note", "correctDoctype": "Note"})
		self.assertFalse(err, out)
		self.assertEqual(out["method"], "DocType")
		frappe.clear_cache(doctype=GADGET)
		self.assertEqual(frappe.get_meta(GADGET).get_field("related_note").options, "Note")
		err, out = run("fix_broken_link", {"doctype": GADGET, "fieldname": "related_note", "correctDoctype": "ERPSmith Test Missing"})
		self.assertEqual(out["type"], "InvalidArguments")
