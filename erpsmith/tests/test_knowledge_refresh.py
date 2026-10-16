from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from erpsmith import install, knowledge, memory
from erpsmith.tests.helpers import server_scripts
from erpsmith.tools.methods import READ_ONLY_FLAG

JOB = {"job_id": knowledge.REFRESH_JOB_ID, "deduplicate": True, "enqueue_after_commit": True}


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
		frappe.local.flags.pop("erpsmith_refresh_queued", None)
		frappe.db.savepoint("refresh_test")

	def tearDown(self):
		frappe.local.flags.pop("erpsmith_refresh_queued", None)  # a savepoint rollback does not run after_rollback
		frappe.flags.in_test = self.in_test
		frappe.local.flags.pop(READ_ONLY_FLAG, None)
		frappe.db.rollback(save_point="refresh_test")

	def test_saving_or_deleting_a_server_script_queues_one_build_without_the_manual(self):
		with server_scripts(), patch("frappe.enqueue") as enqueue:
			doc = frappe.get_doc({"doctype": "Server Script", "name": "ERPSmith Test Refresh Script", "script_type": "DocType Event",
				"reference_doctype": "ToDo", "doctype_event": "Before Save", "disabled": 1, "script": "pass"}).insert()
			doc.delete()
		self.assertEqual(len(builds(enqueue)), 1)  # the save queues it; the delete in the same transaction adds nothing
		for call in builds(enqueue):
			self.assertEqual({k: call.kwargs[k] for k in (*JOB, "include_manual")}, {**JOB, "include_manual": False})

	def test_a_full_build_is_not_swallowed_by_a_queued_refresh(self):
		# Frappe drops a job whose job_id is already queued or running, so the two kinds of build need different ids.
		with patch("frappe.enqueue") as enqueue:
			knowledge.enqueue_build(include_manual=False)
			knowledge.enqueue_build()
		first, second = builds(enqueue)
		self.assertEqual((first.kwargs["job_id"], second.kwargs["job_id"]), (knowledge.REFRESH_JOB_ID, knowledge.FULL_JOB_ID))
		self.assertEqual((first.kwargs["include_manual"], second.kwargs["include_manual"]), (False, True))
		self.assertNotEqual(knowledge.REFRESH_JOB_ID, knowledge.FULL_JOB_ID)

	def test_many_saves_queue_one_build(self):
		with patch("frappe.enqueue") as enqueue:
			for n in range(100):
				client_script(n)
		# Frappe checks `deduplicate` when enqueue is called, not when the after-commit push runs, so 100 calls would be
		# 100 pushes of the same job
		self.assertEqual(len(builds(enqueue)), 1)
		self.assertEqual(builds(enqueue)[0].kwargs["job_id"], knowledge.REFRESH_JOB_ID)

	def test_a_commit_or_rollback_lets_the_next_save_queue_again(self):
		for n, end in enumerate((frappe.db.after_commit, frappe.db.after_rollback)):
			with patch("frappe.enqueue") as enqueue:
				client_script(f"first {n}")
				end.run()  # what a commit or a full rollback runs
				client_script(f"second {n}")
			self.assertEqual(len(builds(enqueue)), 2)
			frappe.local.flags.pop("erpsmith_refresh_queued", None)

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
