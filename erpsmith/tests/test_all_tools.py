import re
from pathlib import Path

import frappe
from frappe.tests.utils import FrappeTestCase

from erpsmith import guards, mcp
from erpsmith.tests.helpers import call, drop_doctype, forget_plans, run, server_scripts

GADGET = "ERPSmith Test Smoke"
SPARE = "ERPSmith Test Spare"
SPARE_RENAMED = "ERPSmith Test Spare Two"
ROLE = "ERPSmith Test Role"
PREFIX = "ERPSmith Test"


class TestEveryToolOverMCP(FrappeTestCase):
	"""Spec success criterion 3: one happy path per tool, all 59, called the way a client calls them."""

	def setUp(self):
		self.smoked = set()

	def tearDown(self):
		frappe.set_user("Administrator")
		for name in frappe.get_all("Workflow", filters={"document_type": GADGET}, pluck="name"):
			frappe.delete_doc("Workflow", name, force=True)
		for doctype, field in (("Server Script", "name"), ("Client Script", "name"), ("Print Format", "name"), ("Report", "name"),
				("Notification", "name"), ("Dashboard Chart", "name"), ("Role", "name"), ("Note", "title")):
			for name in frappe.get_all(doctype, filters={field: ("like", f"{PREFIX}%")}, pluck="name"):
				frappe.delete_doc(doctype, name, force=True)  # delete_doc, not db.delete: it removes the child rows too
		frappe.cache.delete_value("server_script_map")
		for name in (GADGET, SPARE, SPARE_RENAMED):
			drop_doctype(name)  # commits
		forget_plans()

	def step(self, tool, args=None):
		"""Call `tool`; a write runs under its own approved plan, a read without one (which checks it is not gated)."""
		args = args or {}
		err, out = run(tool, args) if guards.is_mutating(tool, args) else call(tool, args)
		self.assertFalse(err, (tool, out))
		self.smoked.add(tool)
		return out

	def test_every_tool_has_a_working_happy_path(self):
		self.assertEqual(len(mcp.SCHEMAS), 59)
		frappe.get_doc({"doctype": "Role", "role_name": ROLE}).insert()
		note = frappe.get_doc({"doctype": "Note", "title": f"{PREFIX} Note"}).insert().name

		# DocTypes and fields (these commit: they change the schema)
		self.step("create_doctype", {"name": GADGET, "module": "Custom", "fields": [
			{"label": "Title", "fieldtype": "Data", "required": True},
			{"label": "Related Note", "fieldtype": "Link", "options": "Note"},
			{"label": "Status", "fieldtype": "Select", "options": "Open\nClosed"}]})
		self.step("create_doctype", {"name": SPARE, "module": "Custom"})
		self.step("rename_doctype", {"oldName": SPARE, "newName": SPARE_RENAMED})
		self.step("delete_doctype", {"name": SPARE_RENAMED})
		self.step("get_doctype", {"name": GADGET})
		self.step("list_doctypes", {"search": PREFIX})
		self.step("add_field", {"doctype": GADGET, "label": "Size", "fieldtype": "Int"})
		self.step("update_field", {"doctype": GADGET, "fieldname": "size", "updates": {"bold": 1}})
		self.step("list_fields", {"doctype": GADGET})
		self.step("reorder_fields", {"doctype": GADGET, "fieldOrder": ["status", "title"]})
		self.step("set_property", {"doctype": GADGET, "property": "allow_rename", "value": 1})
		self.step("remove_field", {"doctype": GADGET, "fieldname": "size"})
		self.step("create_workflow", {"name": f"{PREFIX} Flow", "doctype": GADGET,
			"states": [{"state": "Pending", "allowEdit": "System Manager"}, {"state": "Approved", "allowEdit": "System Manager"}],
			"transitions": [{"from": "Pending", "action": "Approve", "to": "Approved", "allowed": "System Manager"}]})
		self.step("fix_broken_link", {"doctype": GADGET, "fieldname": "related_note", "correctDoctype": "Note"})

		# Records, through the record tools and the fix tools
		doc = self.step("create_record", {"doctype": GADGET, "data": {"title": "first", "related_note": note}})["name"]
		self.step("get_record", {"doctype": GADGET, "name": doc})
		self.step("query_records", {"doctype": GADGET, "fields": ["name", "title"]})
		self.step("update_record", {"doctype": GADGET, "name": doc, "data": {"status": "Closed"}})
		frappe.db.set_value(GADGET, doc, {"workflow_state": "ERPSmith Gone", "title": ""})
		self.step("fix_stuck_document", {"doctype": GADGET, "docName": doc, "targetState": "Approved"})
		self.step("fix_missing_required_value", {"doctype": GADGET, "recordName": doc, "fieldname": "title", "value": "again"})
		self.step("fix_broken_link_value", {"doctype": GADGET, "recordName": doc, "fieldname": "related_note", "correctValue": note})
		self.step("list_workflows", {"doctype": GADGET})
		self.step("debug_workflow", {"doctypeOrWorkflow": GADGET})
		self.step("activate_workflow", {"workflowName": f"{PREFIX} Flow"})
		self.step("set_permission", {"doctype": GADGET, "role": ROLE, "permissions": {"read": 1}})
		self.step("fix_permission", {"doctype": GADGET, "role": ROLE, "permissions": {"write": 1}})
		self.step("debug_permissions", {"doctype": GADGET, "role": ROLE})
		self.step("debug_doctype_fields", {"doctype": GADGET})
		self.step("debug_data_integrity", {"doctype": GADGET})

		# Scripts
		self.step("create_client_script", {"name": f"{PREFIX} Client", "doctype": GADGET, "script": "frappe.ui.form.on('x', {})"})
		self.step("list_client_scripts", {"doctype": GADGET})
		self.step("get_client_script", {"name": f"{PREFIX} Client"})
		self.step("update_client_script", {"name": f"{PREFIX} Client", "enabled": False})
		self.step("debug_client_script", {"nameOrDoctype": GADGET})
		self.step("apply_script_fix", {"scriptName": f"{PREFIX} Client", "scriptType": "client", "fixedScript": "// fixed"})
		with server_scripts():
			self.step("create_server_script", {"name": f"{PREFIX} Server", "scriptType": "API", "apiMethod": "erpsmith_test_smoke",
				"script": "frappe.response['message'] = 1"})
			self.step("update_server_script", {"name": f"{PREFIX} Server", "enabled": False})
			self.step("list_server_scripts", {"scriptType": "API"})
			self.step("get_server_script", {"name": f"{PREFIX} Server"})
			self.step("debug_server_script", {"nameOrDoctype": f"{PREFIX} Server"})
			self.step("apply_script_fix", {"scriptName": f"{PREFIX} Server", "scriptType": "server", "fixedScript": "frappe.response['message'] = 2"})
		self.step("full_health_check", {"doctype": GADGET})

		# Builders, logs and methods
		self.step("create_print_format", {"name": f"{PREFIX} Print", "doctype": GADGET, "htmlContent": "<p>{{ doc.title }}</p>"})
		self.step("create_report", {"name": f"{PREFIX} Report", "doctype": GADGET, "reportType": "Report Builder"})
		self.step("create_notification", {"name": f"{PREFIX} Notice", "doctype": GADGET, "subject": "s", "message": "m",
			"recipients": [{"role": "System Manager"}]})
		self.step("create_dashboard_chart", {"name": f"{PREFIX} Chart", "doctype": GADGET})
		self.step("get_error_logs", {"limit": 1})
		self.step("get_scheduler_logs", {"limit": 1})
		self.step("call_method_read", {"method": "frappe.client.get_count", "args": {"doctype": GADGET}})
		self.step("call_method", {"method": "frappe.client.set_value", "args": {"doctype": GADGET, "name": doc,
			"fieldname": "status", "value": "Open"}})
		self.step("delete_record", {"doctype": GADGET, "name": doc})

		# Memory, knowledge and the plan tools (after the last schema change, so the class rollback removes their rows)
		note_id = self.step("add_memory", {"topic": "pattern", "title": f"{PREFIX} smoke note", "content": "smoke"})["id"]
		self.step("search_memory", {"query": "smoke note"})
		self.step("list_memory", {"topic": "pattern"})
		self.step("update_memory", {"id": note_id, "content": "smoke again"})
		self.step("dump_memory")
		self.step("delete_memory", {"id": note_id})
		self.step("search_erpnext_knowledge", {"query": "sales invoice"})
		plan = self.step("submit_plan", {"summary": "smoke", "changes": [{"tool": "create_record", "args": {"doctype": "ToDo", "data": {"description": "smoke"}}}]})
		self.step("approve_plan", {"planId": plan["planId"]})
		self.step("get_current_plan")

		self.assertEqual(sorted(set(mcp.SCHEMAS) - self.smoked), [])


class TestInstructionsNameRealTools(FrappeTestCase):
	def test_every_tool_named_in_the_instructions_exists(self):
		text = Path(mcp.__file__).with_name("instructions.md").read_text(encoding="utf-8")
		named = {w for w in re.findall(r"(?<![.\w])[a-z]+(?:_[a-z]+)+\b", text)  # not dotted method paths such as frappe.client.set_value
			if w.split("_")[0] in {"get", "list", "query", "search", "debug", "create", "update", "delete", "add", "remove", "set",
				"fix", "apply", "rename", "reorder", "activate", "submit", "approve", "dump", "call", "full"}}
		self.assertTrue(named, "the instructions name no tools?")
		self.assertEqual(sorted(named - set(mcp.SCHEMAS)), [])
