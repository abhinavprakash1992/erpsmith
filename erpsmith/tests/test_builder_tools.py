import frappe
from frappe.tests.utils import FrappeTestCase

from erpsmith.tests.helpers import call, drop_doctype, forget_plans, make_user, run, server_scripts

GADGET = "ERPSmith Test Gadget"
ROLE = "ERPSmith Test Role"
RESTRICTED = "erpsmith-builders@example.com"
PREFIX = "ERPSmith Test"


class TestWorkflow(FrappeTestCase):
	def setUp(self):
		frappe.get_doc({"doctype": "DocType", "name": GADGET, "module": "Custom", "custom": 1, "autoname": "hash",
			"fields": [{"fieldname": "title", "label": "Title", "fieldtype": "Data"}],
			"permissions": [{"role": "System Manager", "read": 1, "write": 1, "create": 1}]}).insert()

	def tearDown(self):
		for name in frappe.get_all("Workflow", filters={"document_type": GADGET}, pluck="name"):
			frappe.delete_doc("Workflow", name, force=True)
		frappe.db.delete("Workflow State", {"name": ("like", f"{PREFIX}%")})
		frappe.db.delete("Workflow Action Master", {"name": ("like", f"{PREFIX}%")})
		drop_doctype(GADGET)
		forget_plans()

	def test_create_workflow_makes_missing_states_and_actions_and_activates_it(self):
		err, out = run("create_workflow", {"name": f"{PREFIX} Approval", "doctype": GADGET,
			"states": [{"state": f"{PREFIX} Draft", "style": "Warning", "allowEdit": "System Manager"},
				{"state": "Approved", "allowEdit": "System Manager"}],
			"transitions": [{"from": f"{PREFIX} Draft", "action": f"{PREFIX} Approve", "to": "Approved", "allowed": "System Manager"}]})
		self.assertFalse(err, out)
		self.assertEqual(out["createdStates"], [f"{PREFIX} Draft"])  # Approved ships with Frappe
		self.assertEqual(out["createdActions"], [f"{PREFIX} Approve"])
		self.assertEqual(frappe.db.get_value("Workflow State", f"{PREFIX} Draft", "style"), "Warning")
		self.assertEqual(frappe.db.get_value("Workflow", f"{PREFIX} Approval", "is_active"), 1)
		frappe.clear_cache(doctype=GADGET)
		self.assertTrue(frappe.get_meta(GADGET).get_field("workflow_state"))

		err, listed = call("list_workflows", {"doctype": GADGET})
		self.assertFalse(err, listed)
		self.assertEqual([w["name"] for w in listed["workflows"]], [f"{PREFIX} Approval"])

	def test_states_and_transitions_must_be_complete(self):
		for states, transitions in (([], []), ([{"style": "Warning"}], []), ([{"state": "Approved"}], [{"from": "Approved"}])):
			err, out = run("create_workflow", {"name": f"{PREFIX} Bad", "doctype": GADGET, "states": states, "transitions": transitions})
			self.assertEqual(out["type"], "InvalidArguments", out)


class TestBuilders(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("Role", ROLE):
			frappe.get_doc({"doctype": "Role", "role_name": ROLE}).insert()
		make_user(RESTRICTED)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.delete("Custom DocPerm", {"parent": "ToDo"})  # the tests of one class share a transaction
		frappe.clear_cache(doctype="ToDo")

	def test_set_permission_keeps_every_other_role(self):
		own = {(p.role, p.permlevel): p.read for p in frappe.get_all("DocPerm", filters={"parent": "ToDo"}, fields=["role", "permlevel", "read"])}
		self.assertFalse(frappe.db.exists("Custom DocPerm", {"parent": "ToDo"}))
		err, out = run("set_permission", {"doctype": "ToDo", "role": ROLE, "permissions": {"write": 1}})
		self.assertFalse(err, out)
		self.assertEqual((out["permissions"]["read"], out["permissions"]["write"], out["permissions"]["create"]), (1, 1, 0))
		custom = {(p.role, p.permlevel): p.read for p in frappe.get_all("Custom DocPerm", filters={"parent": "ToDo"},
			fields=["role", "permlevel", "read"])}
		self.assertEqual(custom, {**own, (ROLE, 0): 1})
		err, got = call("get_doctype", {"name": "ToDo"})
		self.assertEqual(got["permissionSource"], "Custom DocPerm")

		err, out = run("set_permission", {"doctype": "ToDo", "role": ROLE, "permissions": {"write": 0}})
		self.assertFalse(err, out)
		self.assertEqual(frappe.db.count("Custom DocPerm", {"parent": "ToDo", "role": ROLE}), 1)
		self.assertEqual(out["permissions"]["write"], 0)

	def test_set_permission_needs_a_real_role_known_types_and_the_system_manager_role(self):
		for args in ({"doctype": "ToDo", "role": "ERPSmith No Such Role"},
				{"doctype": "ToDo", "role": ROLE, "permissions": {"fly": 1}},
				{"doctype": "ToDo", "role": ROLE, "permissions": "write"}):
			err, out = run("set_permission", args)
			self.assertEqual(out["type"], "InvalidArguments", out)
		frappe.set_user(RESTRICTED)
		err, out = run("set_permission", {"doctype": "ToDo", "role": ROLE})
		self.assertEqual(out["type"], "PermissionError", out)
		self.assertIn("System Manager", out["message"])
		frappe.set_user("Administrator")
		self.assertFalse(frappe.db.exists("Custom DocPerm", {"parent": "ToDo"}))

	def test_print_format_uses_its_html(self):
		err, out = run("create_print_format", {"name": f"{PREFIX} Print", "doctype": "ToDo", "htmlContent": "<p>{{ doc.name }}</p>"})
		self.assertFalse(err, out)
		row = frappe.db.get_value("Print Format", f"{PREFIX} Print", ["custom_format", "standard", "print_format_type", "html"], as_dict=True)
		self.assertEqual((row.custom_format, row.standard, row.print_format_type), (1, "No", "Jinja"))
		err, out = run("create_print_format", {"name": f"{PREFIX} Broken", "doctype": "ToDo", "htmlContent": "<p>{{ doc.name </p>"})
		self.assertTrue(err)
		self.assertFalse(frappe.db.exists("Print Format", f"{PREFIX} Broken"))

	def test_reports(self):
		err, out = run("create_report", {"name": f"{PREFIX} Query", "doctype": "ToDo", "reportType": "Query Report",
			"query": "select name from `tabToDo`"})
		self.assertFalse(err, out)
		self.assertEqual(frappe.db.get_value("Report", f"{PREFIX} Query", "query"), "select name from `tabToDo`")

		script = {"name": f"{PREFIX} Script", "doctype": "ToDo", "script": "result = [[1]]",
			"columns": [{"fieldname": "n", "label": "N", "fieldtype": "Int"}]}
		with server_scripts(False):
			err, out = run("create_report", script)
		self.assertTrue(out["message"].startswith("Server scripts are disabled"))
		with server_scripts():
			err, out = run("create_report", {**script, "script": "def f():\n    return filters\nresult = f()"})
			self.assertEqual(out["type"], "UnsafeScript")
			err, out = run("create_report", script)
		self.assertFalse(err, out)
		doc = frappe.get_doc("Report", f"{PREFIX} Script")
		self.assertEqual((doc.report_type, doc.report_script, len(doc.columns)), ("Script Report", "result = [[1]]", 1))

	def test_notification_has_one_channel_and_its_recipients(self):
		err, out = run("create_notification", {"name": f"{PREFIX} Notice", "doctype": "ToDo", "subject": "New ToDo",
			"message": "{{ doc.description }}", "recipients": [{"role": "System Manager"}, {"field": "allocated_to"}]})
		self.assertFalse(err, out)
		self.assertEqual((out["event"], out["channel"]), ("New", "Email"))
		doc = frappe.get_doc("Notification", f"{PREFIX} Notice")
		self.assertEqual([(r.receiver_by_role, r.receiver_by_document_field) for r in doc.recipients],
			[("System Manager", None), (None, "allocated_to")])
		err, out = run("create_notification", {"name": f"{PREFIX} Two", "doctype": "ToDo", "subject": "s", "message": "m",
			"channels": ["Email", "SMS"]})
		self.assertEqual(out["type"], "InvalidArguments")

	def test_dashboard_charts(self):
		err, out = run("create_dashboard_chart", {"name": f"{PREFIX} Over Time", "doctype": "ToDo"})
		self.assertFalse(err, out)
		self.assertEqual((out["chartType"], out["basis"]), ("Bar", "Count"))
		row = frappe.db.get_value("Dashboard Chart", f"{PREFIX} Over Time", ["based_on", "timeseries", "timespan"], as_dict=True)
		self.assertEqual((row.based_on, row.timeseries, row.timespan), ("creation", 1, "Last Year"))
		err, out = run("create_dashboard_chart", {"name": f"{PREFIX} By Status", "doctype": "ToDo", "chartType": "Pie", "groupBy": "status"})
		self.assertFalse(err, out)
		self.assertEqual((out["chartType"], out["basis"]), ("Pie", "Group By"))
