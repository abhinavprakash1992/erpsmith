import frappe
from frappe.tests.utils import FrappeTestCase

from erpsmith.tests.helpers import call, run

PROBE = "erpsmith call_method probe"


@frappe.whitelist()
def write_probe(description=PROBE):
	return frappe.get_doc({"doctype": "ToDo", "description": description}).insert()


@frappe.whitelist()
def commit_twice():
	"""A method that ignores the refused commit and commits again."""
	frappe.get_doc({"doctype": "ToDo", "description": PROBE}).insert()
	try:
		frappe.db.commit()
	except frappe.ValidationError:
		pass
	frappe.db.commit()


@frappe.whitelist()
def echo_form_dict():
	return dict(frappe.form_dict)


def not_whitelisted():
	return 1


HERE = "erpsmith.tests.test_methods"


class TestCallMethod(FrappeTestCase):
	def tearDown(self):
		frappe.db.delete("ToDo", {"description": PROBE})

	def test_call_method_runs_a_whitelisted_method_and_keeps_its_writes(self):
		err, out = run("call_method", {"method": f"{HERE}.write_probe"})
		self.assertFalse(err, out)
		self.assertEqual(out["result"]["description"], PROBE)  # a returned document comes back as a dict
		self.assertTrue(frappe.db.exists("ToDo", out["result"]["name"]))

	def test_a_method_returning_a_frappe_dict_comes_back_as_is(self):
		todo = frappe.get_doc({"doctype": "ToDo", "description": PROBE}).insert()
		err, out = run("call_method", {"method": "frappe.client.set_value",
			"args": {"doctype": "ToDo", "name": todo.name, "fieldname": "status", "value": "Closed"}})
		self.assertFalse(err, out)
		self.assertEqual(out["result"]["status"], "Closed")

	def test_call_method_is_gated(self):
		err, out = call("call_method", {"method": "frappe.client.get_count", "args": {"doctype": "ToDo"}})
		self.assertTrue(err)
		self.assertIn("BLOCKED by plan gate", out["message"])

	def test_call_method_read_rolls_back_and_needs_no_plan(self):
		err, out = call("call_method_read", {"method": f"{HERE}.write_probe"})
		self.assertFalse(err, out)
		self.assertTrue(out["rolledBack"])
		self.assertEqual(out["result"]["description"], PROBE)
		self.assertFalse(frappe.db.exists("ToDo", {"description": PROBE}))
		err, out = call("call_method_read", {"method": "frappe.client.get_count", "args": {"doctype": "User"}})
		self.assertFalse(err, out)
		self.assertGreater(out["result"], 0)

	def test_call_method_read_refuses_a_method_that_commits_even_twice(self):
		err, out = call("call_method_read", {"method": f"{HERE}.commit_twice"})
		self.assertTrue(err)
		self.assertIn("call_method_read rolls back", out["message"])
		self.assertFalse(frappe.db.exists("ToDo", {"description": PROBE}))
		frappe.db.before_commit.run()  # what the request's own commit runs first: it would raise if the refusal were still queued

	def test_methods_that_read_form_dict_see_the_arguments(self):
		err, out = call("call_method_read", {"method": f"{HERE}.echo_form_dict", "args": {"doctype": "ToDo", "items": "[]"}})
		self.assertFalse(err, out)
		self.assertEqual(out["result"], {"doctype": "ToDo", "items": "[]"})

	def test_unknown_and_unwhitelisted_methods_are_refused(self):
		err, out = call("call_method_read", {"method": "frappe.no_such_method"})
		self.assertEqual(out["type"], "InvalidArguments")
		err, out = call("call_method_read", {"method": "no_such_app.thing"})
		self.assertEqual(out["type"], "InvalidArguments")
		err, out = call("call_method_read", {"method": f"{HERE}.not_whitelisted"})
		self.assertEqual(out["type"], "PermissionError")
		self.assertIn("not whitelisted", out["message"])
		self.assertIn("whitelisted", out["suggestion"])
		err, out = call("call_method_read", {"method": "frappe.client.get_count", "args": ["ToDo"]})
		self.assertEqual(out["type"], "InvalidArguments")
