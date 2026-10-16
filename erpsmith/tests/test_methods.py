import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import background_jobs

from erpsmith.tests.helpers import call, run
from erpsmith.tools import methods

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


@frappe.whitelist()
def queue_a_job():
	frappe.enqueue("frappe.ping")


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
		self.assertIs(out["rolledBack"], True)
		self.assertEqual(out["result"]["description"], PROBE)
		self.assertFalse(frappe.db.exists("ToDo", {"description": PROBE}))
		frappe.db.before_commit.run()  # what the request's own commit runs: nothing of the call may still be queued
		frappe.db.after_commit.run()
		err, out = call("call_method_read", {"method": "frappe.client.get_count", "args": {"doctype": "User"}})
		self.assertFalse(err, out)
		self.assertGreater(out["result"], 0)

	def test_call_method_read_refuses_a_method_that_commits_even_twice(self):
		# called directly: mcp.call_tool's own error path also resets the commit callbacks, which would hide a missing cleanup here
		with self.assertRaisesRegex(frappe.ValidationError, "call_method_read rolls back"):
			methods.call_method_read(method=f"{HERE}.commit_twice")
		self.assertFalse(frappe.db.exists("ToDo", {"description": PROBE}))
		frappe.db.before_commit.run()  # what the request's own commit runs first: it would raise if the refusal were still queued

	def test_call_method_read_refuses_a_method_that_queues_a_background_job(self):
		get_queue = background_jobs.get_queue
		err, out = call("call_method_read", {"method": f"{HERE}.queue_a_job"})
		self.assertTrue(err)
		self.assertIn("background job", out["message"])
		self.assertIs(background_jobs.get_queue, get_queue)
		self.assertFalse(frappe.local.flags.get(methods.READ_ONLY_FLAG))  # the refusal ends with the call
		self.assertTrue(background_jobs.get_queue("default"))  # and queues work again afterwards

	def test_the_list_views_bulk_delete_is_refused_by_call_method_read(self):
		names = [frappe.get_doc({"doctype": "ToDo", "description": PROBE}).insert().name for _ in range(11)]
		err, out = call("call_method_read", {"method": "frappe.desk.reportview.delete_items",
			"args": {"doctype": "ToDo", "items": json.dumps(names)}})
		self.assertTrue(err)
		self.assertIn("background job", out["message"])
		self.assertEqual(frappe.db.count("ToDo", {"name": ("in", names)}), 11)

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
