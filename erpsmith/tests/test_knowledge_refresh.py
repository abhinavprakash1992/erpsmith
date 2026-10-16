from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from erpsmith import install, knowledge, memory
from erpsmith.tests.helpers import server_scripts
from erpsmith.tools.methods import READ_ONLY_FLAG

JOB = {"job_id": "erpsmith-knowledge-build", "deduplicate": True, "enqueue_after_commit": True}


def builds(enqueue):
	"""The calls that queue the knowledge build (Frappe queues jobs of its own while it deletes)."""
	return [c for c in enqueue.call_args_list if c.args == ("erpsmith.knowledge.build",)]


def client_script(n):
	return frappe.get_doc({"doctype": "Client Script", "name": f"ERPSmith Test Refresh {n}", "dt": "ToDo",
		"view": "Form", "enabled": 0, "script": "// nothing"}).insert()


class TestRefresh(FrappeTestCase):
	def setUp(self):
		self.in_test = frappe.flags.in_test
		frappe.flags.in_test = False  # the hook skips test runs; these tests turn it on
		frappe.db.savepoint("refresh_test")

	def tearDown(self):
		frappe.flags.in_test = self.in_test
		frappe.local.flags.pop(READ_ONLY_FLAG, None)
		frappe.db.rollback(save_point="refresh_test")

	def test_saving_or_deleting_a_server_script_queues_one_build_without_the_manual(self):
		with server_scripts(), patch("frappe.enqueue") as enqueue:
			doc = frappe.get_doc({"doctype": "Server Script", "name": "ERPSmith Test Refresh Script", "script_type": "DocType Event",
				"reference_doctype": "ToDo", "doctype_event": "Before Save", "disabled": 1, "script": "pass"}).insert()
			doc.delete()
		self.assertGreaterEqual(len(builds(enqueue)), 2)  # the save and the delete
		for call in builds(enqueue):
			self.assertEqual({k: call.kwargs[k] for k in (*JOB, "include_manual")}, {**JOB, "include_manual": False})

	def test_many_saves_queue_one_build(self):
		with patch("frappe.enqueue") as enqueue:
			for n in range(100):
				client_script(n)
		self.assertEqual(len(builds(enqueue)), 100)
		self.assertEqual({(c.kwargs["job_id"], c.kwargs["deduplicate"]) for c in builds(enqueue)},
			{("erpsmith-knowledge-build", True)})  # Frappe drops the duplicates

	def test_no_build_is_queued_inside_call_method_read(self):
		frappe.local.flags[READ_ONLY_FLAG] = True
		with patch("frappe.enqueue") as enqueue:
			client_script("readonly")
		enqueue.assert_not_called()

	def test_the_hooks_cover_every_source_doctype(self):
		events = frappe.get_hooks("doc_events")
		for name in knowledge.REFRESH_DOCTYPES:
			for event in ("on_update", "on_trash"):
				handlers = events[name][event]
				self.assertIn("erpsmith.knowledge.on_source_change", handlers if isinstance(handlers, list) else [handlers])

	def test_tests_do_not_queue_builds(self):
		frappe.flags.in_test = True
		with patch("frappe.enqueue") as enqueue:
			client_script("in-test")
		enqueue.assert_not_called()

	def test_install_still_queues_the_full_build(self):
		with patch("frappe.enqueue") as enqueue, patch.object(memory, "load_seeds"):
			install.after_migrate()
		enqueue.assert_called_once()
		self.assertTrue(enqueue.call_args.kwargs["include_manual"])
