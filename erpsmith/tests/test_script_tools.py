import frappe
from frappe.tests.utils import FrappeTestCase

from erpsmith.tools.scripts import SCRIPTS_DISABLED
from erpsmith.tests.helpers import call, run, server_scripts

PREFIX = "ERPSmith Test"
TARGET = "Note"  # no other test saves a Note, so a test script attached to it never runs by accident


class TestScriptTools(FrappeTestCase):
	def tearDown(self):
		frappe.db.delete("Server Script", {"name": ("like", f"{PREFIX}%")})
		frappe.db.delete("Client Script", {"name": ("like", f"{PREFIX}%")})
		frappe.cache.delete_value("server_script_map")  # Redis is not rolled back with the database

	def test_client_script_round_trip(self):
		err, out = run("create_client_script", {"name": f"{PREFIX} Form", "doctype": TARGET,
			"script": "frappe.ui.form.on('Note', {refresh(frm) {}})"})
		self.assertFalse(err, out)
		self.assertEqual((out["doctype"], out["scriptType"], out["enabled"]), (TARGET, "Form", True))

		err, listed = call("list_client_scripts", {"doctype": TARGET})
		self.assertFalse(err, listed)
		self.assertIn(f"{PREFIX} Form", [s["name"] for s in listed["scripts"]])

		err, out = run("update_client_script", {"name": f"{PREFIX} Form", "script": "// v2", "enabled": False})
		self.assertFalse(err, out)
		self.assertFalse(out["enabled"])
		err, got = call("get_client_script", {"name": f"{PREFIX} Form"})
		self.assertEqual((got["script"], got["enabled"]), ("// v2", False))

		err, out = run("apply_script_fix", {"scriptName": f"{PREFIX} Form", "scriptType": "client", "fixedScript": "// v3"})
		self.assertFalse(err, out)
		self.assertTrue(out["enabled"])
		self.assertEqual(frappe.db.get_value("Client Script", f"{PREFIX} Form", "script"), "// v3")

	def test_client_script_name_defaults_and_unknown_views_are_refused(self):
		err, out = run("create_client_script", {"doctype": TARGET, "script": "// x", "scriptType": "List"})
		self.assertFalse(err, out)
		self.assertEqual(out["name"], f"{TARGET} List Script")
		frappe.db.delete("Client Script", {"name": out["name"]})
		err, out = run("create_client_script", {"doctype": TARGET, "script": "// x", "scriptType": "Global"})
		self.assertEqual(out["type"], "InvalidArguments")

	def test_server_script_round_trip_strips_global_imports(self):
		args = {"name": f"{PREFIX} Event", "scriptType": "DocType Event", "doctype": TARGET, "event": "Before Save",
			"script": "import json\ndoc.content = doc.content or json.dumps({})"}
		with server_scripts():
			err, out = run("create_server_script", args)
		self.assertFalse(err, out)
		self.assertEqual((out["doctype"], out["event"], out["enabled"]), (TARGET, "Before Save", True))
		self.assertEqual(out["removedImports"], ["import json"])
		self.assertTrue(frappe.db.get_value("Server Script", f"{PREFIX} Event", "script").startswith("pass  # import removed"))

		err, listed = call("list_server_scripts", {"doctype": TARGET})  # read tools work while scripts are disabled
		self.assertFalse(err, listed)
		self.assertIn(f"{PREFIX} Event", [s["name"] for s in listed["scripts"]])

		with server_scripts():
			err, out = run("update_server_script", {"name": f"{PREFIX} Event", "enabled": False, "event": "After Save"})
		self.assertFalse(err, out)
		self.assertEqual((out["enabled"], out["event"]), (False, "After Save"))

		with server_scripts():
			err, out = run("apply_script_fix", {"scriptName": f"{PREFIX} Event", "scriptType": "server",
				"fixedScript": "doc.content = doc.content or 'x'"})
		self.assertFalse(err, out)
		self.assertTrue(out["enabled"])
		err, got = call("get_server_script", {"name": f"{PREFIX} Event"})
		self.assertEqual(got["script"], "doc.content = doc.content or 'x'")

	def test_api_script_gets_its_method_and_no_guest_access(self):
		with server_scripts():
			err, out = run("create_server_script", {"name": f"{PREFIX} API", "scriptType": "API",
				"apiMethod": "erpsmith_test_ping", "script": "frappe.response['message'] = 'pong'"})
		self.assertFalse(err, out)
		self.assertEqual(out["apiMethod"], "erpsmith_test_ping")
		self.assertEqual(frappe.db.get_value("Server Script", f"{PREFIX} API", "allow_guest"), 0)

	def test_a_script_the_sandbox_would_reject_is_not_saved(self):
		with server_scripts():
			err, out = run("create_server_script", {"name": f"{PREFIX} Bad", "scriptType": "API", "apiMethod": "erpsmith_test_bad",
				"script": "x = hasattr(frappe, 'db')\nclass A:\n    pass"})
		self.assertTrue(err)
		self.assertEqual(out["type"], "UnsafeScript")
		self.assertIn("Line 1: hasattr", out["message"])
		self.assertIn("Line 2: class", out["message"])
		self.assertIn("RULE 1", out["suggestion"])
		self.assertFalse(frappe.db.exists("Server Script", f"{PREFIX} Bad"))

	def test_a_bench_without_server_scripts_gets_the_enable_command(self):
		with server_scripts(False):
			err, out = run("create_server_script", {"name": f"{PREFIX} Off", "scriptType": "API", "apiMethod": "erpsmith_test_off",
				"script": "frappe.response['message'] = 1"})
		self.assertTrue(err)
		self.assertEqual(out["message"], SCRIPTS_DISABLED)
		self.assertIn("bench administrator", out["suggestion"])
		self.assertFalse(frappe.db.exists("Server Script", f"{PREFIX} Off"))

	def test_missing_arguments_are_invalid(self):
		for tool, args in (("update_server_script", {"name": f"{PREFIX} X"}), ("update_client_script", {"name": f"{PREFIX} X"}),
				("apply_script_fix", {"scriptName": f"{PREFIX} X", "scriptType": "python", "fixedScript": "x = 1"}),
				("create_server_script", {"name": f"{PREFIX} X", "scriptType": "DocType Event", "script": "x = 1"})):
			with server_scripts():
				err, out = run(tool, args)
			self.assertEqual(out["type"], "InvalidArguments", (tool, out))
