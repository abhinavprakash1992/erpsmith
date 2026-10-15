from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from erpsmith.tools.scripts import SCRIPTS_DISABLED
from erpsmith.tests.helpers import call, drop_doctype, server_scripts

GADGET = "ERPSmith Test Gadget"
PART = "ERPSmith Test Gadget Part"
WORKFLOW = "ERPSmith Test Gadget Flow"
ROLE = "ERPSmith Test Role"


def types(report):
	return {i["type"] for i in report["issues"]}


class TestDebugTools(FrappeTestCase):
	"""One scratch DocType with a workflow for the whole class (they commit); each test runs inside a savepoint."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.get_doc({"doctype": "DocType", "name": PART, "module": "Custom", "custom": 1, "istable": 1,
			"fields": [{"fieldname": "item", "label": "Item", "fieldtype": "Data"}]}).insert()
		frappe.get_doc({"doctype": "DocType", "name": GADGET, "module": "Custom", "custom": 1, "autoname": "hash",
			"fields": [
				{"fieldname": "title", "label": "Title", "fieldtype": "Data", "reqd": 1},
				{"fieldname": "related_note", "label": "Related Note", "fieldtype": "Link", "options": "Note"},
				{"fieldname": "ref", "label": "Ref", "fieldtype": "Link", "options": "Note"},
				{"fieldname": "kind", "label": "Kind", "fieldtype": "Select", "options": "A\nB"},
				{"fieldname": "notes", "label": "Notes", "fieldtype": "Small Text"},
			],
			"permissions": [{"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1}]}).insert()
		frappe.get_doc({"doctype": "Workflow", "workflow_name": WORKFLOW, "document_type": GADGET, "workflow_state_field": "workflow_state",
			"is_active": 1, "states": [{"state": "Pending", "doc_status": "0", "allow_edit": "System Manager"},
				{"state": "Approved", "doc_status": "0", "allow_edit": "System Manager"}],
			"transitions": [{"state": "Pending", "action": "Approve", "next_state": "Approved", "allowed": "All"}]}).insert()
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		super().tearDownClass()
		if frappe.db.exists("Workflow", WORKFLOW):
			frappe.delete_doc("Workflow", WORKFLOW, force=True)
		frappe.db.delete("Error Log", {"method": ("like", "erpsmith_test%")})  # Frappe commits Error Log rows at once
		drop_doctype(GADGET)  # commits
		drop_doctype(PART)

	def setUp(self):
		frappe.db.savepoint("debug_test")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback(save_point="debug_test")
		frappe.clear_cache(doctype=GADGET)
		frappe.cache.delete_value("server_script_map")

	def gadget(self, **values):
		return frappe.get_doc({"doctype": GADGET, "title": "t", **values}).insert()

	def test_doctype_fields_reports_each_kind_of_broken_field(self):
		err, out = call("debug_doctype_fields", {"doctype": GADGET})
		self.assertFalse(err, out)
		self.assertEqual(out["status"], "clean", out["issues"])

		for column, value, fieldname in (("options", "ERPSmith Test Missing", "ref"), ("options", "", "kind"),
				("depends_on", "eval:doc.no_such_field == 1", "notes"), ("fieldname", "Bad Name", "notes")):
			frappe.db.sql(f"update `tabDocField` set `{column}`=%s where parent=%s and fieldname=%s", (value, GADGET, fieldname))
		frappe.clear_cache(doctype=GADGET)
		err, out = call("debug_doctype_fields", {"doctype": GADGET})
		self.assertFalse(err, out)
		self.assertEqual(types(out), {"broken_link", "empty_select_options", "broken_depends_on", "invalid_fieldname"})
		self.assertEqual(out["status"], "errors")
		self.assertIn("fix_broken_link", [f.get("tool") for f in out["fixes"]])

	def test_depends_on_may_use_standard_columns(self):
		frappe.db.sql("update `tabDocField` set depends_on=%s where parent=%s and fieldname='notes'",
			("eval:doc.docstatus==0 && !doc.__islocal && doc.name && doc.owner && doc.creation", GADGET))
		frappe.clear_cache(doctype=GADGET)
		err, out = call("debug_doctype_fields", {"doctype": GADGET})
		self.assertFalse(err, out)
		self.assertNotIn("broken_depends_on", types(out))

	def test_permissions_for_a_role_and_for_a_child_table(self):
		frappe.get_doc({"doctype": "Role", "role_name": ROLE}).insert()
		err, out = call("debug_permissions", {"doctype": GADGET})
		self.assertFalse(err, out)
		self.assertEqual((out["status"], out["permissionSource"]), ("clean", "DocPerm"))
		err, out = call("debug_permissions", {"doctype": GADGET, "role": ROLE})
		self.assertEqual(types(out), {"role_no_read", "role_no_write"})
		err, out = call("debug_permissions", {"doctype": GADGET, "role": "ERPSmith No Such Role"})
		self.assertIn("role_not_found", types(out))
		err, out = call("debug_permissions", {"doctype": PART})
		self.assertEqual(out["status"], "clean")
		self.assertIn("parent", out["note"])

	def test_workflow_reports_inactive_open_actions_and_stuck_documents(self):
		err, out = call("debug_workflow", {"doctypeOrWorkflow": GADGET})
		self.assertFalse(err, out)
		self.assertEqual(out["workflow"], WORKFLOW)
		self.assertEqual(types(out), {"transition_open_to_all"})

		stuck = self.gadget()
		self.assertEqual(stuck.workflow_state, "Pending")  # the active workflow set its first state
		stuck.db_set("workflow_state", "ERPSmith Gone")
		frappe.db.set_value("Workflow", WORKFLOW, "is_active", 0)
		err, out = call("debug_workflow", {"doctypeOrWorkflow": WORKFLOW})
		self.assertEqual(types(out), {"transition_open_to_all", "workflow_inactive", "stuck_documents"})
		stuck_issue = next(i for i in out["issues"] if i["type"] == "stuck_documents")
		self.assertEqual(stuck_issue["documents"], [{"name": stuck.name, "state": "ERPSmith Gone"}])

	def test_data_integrity_finds_empty_required_values_and_deleted_links(self):
		note = frappe.get_doc({"doctype": "Note", "title": "ERPSmith Test Note"}).insert()
		linked = self.gadget(related_note=note.name)
		emptied = self.gadget()
		frappe.db.set_value(GADGET, emptied.name, "title", "")
		frappe.db.delete("Note", {"name": note.name})
		err, out = call("debug_data_integrity", {"doctype": GADGET})
		self.assertFalse(err, out)
		self.assertEqual(out["summary"], {"broken_link_value": 1, "missing_required_value": 1})
		self.assertEqual({(i["record"], i["type"]) for i in out["issues"]},
			{(linked.name, "broken_link_value"), (emptied.name, "missing_required_value")})
		err, out = call("debug_data_integrity", {"doctype": GADGET, "limit": 0})  # clamped up to 1
		self.assertEqual(out["recordsChecked"], 1)
		for doctype in (PART, "System Settings"):
			err, out = call("debug_data_integrity", {"doctype": doctype})
			self.assertEqual(out["type"], "InvalidArguments", out)

	def test_client_script_checks(self):
		frappe.get_doc({"doctype": "Client Script", "name": "ERPSmith Test Messy", "dt": GADGET, "view": "Form", "enabled": 0,
			"script": "function go() {\n  console.log(cur_frm.doc);\n}\nrefresh() {"}).insert()
		err, out = call("debug_client_script", {"nameOrDoctype": GADGET})
		self.assertFalse(err, out)
		self.assertEqual(types(out["scripts"][0]), {"missing_wrapper", "syntax_unbalanced_braces", "console_log_found",
			"missing_frm_param", "cur_frm_usage", "script_disabled"})
		self.assertIn("frappe.ui.form.on('ERPSmith Test Gadget'", out["scripts"][0]["fixes"][0]["fixedScript"])
		err, out = call("debug_client_script", {"nameOrDoctype": "ERPSmith Test Nothing"})
		self.assertEqual(out["type"], "DoesNotExistError")

	def test_server_script_checks_include_the_sandbox_errors_and_past_errors(self):
		frappe.get_doc({"doctype": "Server Script", "name": "ERPSmith Test Event", "script_type": "DocType Event",
			"reference_doctype": GADGET, "doctype_event": "Before Save", "disabled": 1,
			"script": "x = hasattr(frappe, 'db')\ntry:\n    pass\nexcept:\n    pass\nfrappe.throw()"}).insert()
		frappe.get_doc({"doctype": "Error Log", "method": "erpsmith_test_server",
			"error": 'Traceback\n  File "<serverscript>: erpsmith_test_event", line 1\nNameError: boom'}).insert(ignore_permissions=True)
		with server_scripts(False):
			err, out = call("debug_server_script", {"nameOrDoctype": "ERPSmith Test Event"})
		self.assertFalse(err, out)
		script = out["scripts"][0]
		self.assertEqual(types(script), {"sandbox", "bare_except", "empty_throw", "no_doc_reference", "previous_error", "script_disabled"})
		self.assertIn("NameError: boom", next(i for i in script["issues"] if i["type"] == "previous_error")["message"])
		self.assertEqual(out["bench"], SCRIPTS_DISABLED)
		self.assertEqual(out["totalIssues"], script["issueCount"] + 1)
		with server_scripts():
			self.assertNotIn("bench", call("debug_server_script", {"nameOrDoctype": GADGET})[1])

	def test_full_health_check_runs_every_check(self):
		err, out = call("full_health_check", {"doctype": GADGET})
		self.assertFalse(err, out)
		self.assertEqual(set(out["checks"]), {"fields", "clientScripts", "serverScripts", "permissions", "workflow"})
		self.assertEqual(out["checks"]["clientScripts"], {"skipped": "No client scripts"})
		self.assertEqual(out["checks"]["workflow"]["workflow"], WORKFLOW)
		self.assertEqual(out["totalIssues"], 1)  # the workflow's open action
		err, out = call("full_health_check", {"doctype": "ERPSmith Test Nothing"})
		self.assertEqual(out["type"], "DoesNotExistError")

	def test_a_failed_sub_check_makes_the_health_check_incomplete(self):
		with patch("erpsmith.tools.debug.debug_permissions", side_effect=RuntimeError("boom")):
			err, out = call("full_health_check", {"doctype": GADGET})
		self.assertFalse(err, out)
		self.assertEqual((out["status"], out["failedChecks"]), ("incomplete", ["permissions"]))
		self.assertEqual(out["checks"]["permissions"], {"error": "boom"})
		self.assertEqual(call("full_health_check", {"doctype": GADGET})[1]["failedChecks"], [])

	def test_error_and_scheduler_logs(self):
		method = f"erpsmith_test_method_{frappe.generate_hash(length=8)}"  # Error Log rows survive rollbacks, so earlier runs leave some
		frappe.get_doc({"doctype": "Error Log", "method": method, "reference_doctype": "ToDo",
			"error": "x" * 3000 + "\nValueError: the end"}).insert(ignore_permissions=True)
		err, out = call("get_error_logs", {"method": method, "doctype": "ToDo"})
		self.assertFalse(err, out)
		self.assertEqual(out["count"], 1)
		self.assertTrue(out["logs"][0]["error"].endswith("ValueError: the end"))
		self.assertEqual(len(out["logs"][0]["error"]), 1000)
		self.assertEqual(call("get_error_logs", {"method": method, "since": "2999-01-01"})[1]["count"], 0)
		self.assertEqual(call("get_error_logs", {"since": "not a date"})[1]["type"], "InvalidArguments")

		job = frappe.get_all("Scheduled Job Type", pluck="name", limit=1)[0]
		frappe.get_doc({"doctype": "Scheduled Job Log", "scheduled_job_type": job, "status": "Failed", "details": "boom"}).insert(ignore_permissions=True)
		err, out = call("get_scheduler_logs", {"limit": 1})
		self.assertFalse(err, out)
		self.assertEqual((out["count"], out["logs"][0]["status"], out["logs"][0]["details"]), (1, "Failed", "boom"))
