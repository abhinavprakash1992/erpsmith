import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from erpsmith import install, memory, vectors
from erpsmith.tests.helpers import counted_sessions, forget_seeds

FIXTURES = Path(__file__).parent / "fixtures"
V1, V2 = FIXTURES / "seed_test.json", FIXTURES / "seed_test_v2.json"


class TestSeedLoader(FrappeTestCase):
	def setUp(self):
		forget_seeds()

	def test_first_load_inserts_every_seed_with_a_vector(self):
		self.assertEqual(memory.load_seeds(V1), {"inserted": 3, "already_present": 0})
		doc = frappe.get_doc("MCP Memory", "seed__test-sandbox-imports")
		self.assertEqual((doc.seed_key, doc.topic), ("test-sandbox-imports", "sandbox-rule"))
		self.assertEqual(len(vectors.from_b64(doc.vector)), vectors.DIM)

	def test_a_second_load_inserts_nothing_and_never_embeds(self):
		memory.load_seeds(V1)
		with patch("erpsmith.vectors.embed") as embed:
			self.assertEqual(memory.load_seeds(V1), {"inserted": 0, "already_present": 0})
		embed.assert_not_called()

	def test_a_deleted_seed_stays_deleted(self):
		memory.load_seeds(V1)
		frappe.delete_doc("MCP Memory", "seed__test-rename-doc")
		self.assertEqual(memory.load_seeds(V1)["inserted"], 0)
		self.assertFalse(frappe.db.exists("MCP Memory", "seed__test-rename-doc"))

	def test_a_seed_added_in_a_later_release_arrives(self):
		memory.load_seeds(V1)
		self.assertEqual(memory.load_seeds(V2), {"inserted": 1, "already_present": 0})
		self.assertTrue(frappe.db.exists("MCP Memory", "seed__test-later-release"))

	def test_a_user_edit_is_not_overwritten_by_a_later_load(self):
		memory.load_seeds(V1)
		frappe.db.set_value("MCP Memory", "seed__test-child-rows", "content", "my own wording")
		memory.load_seeds(V2)
		self.assertEqual(frappe.db.get_value("MCP Memory", "seed__test-child-rows", "content"), "my own wording")

	def test_a_note_that_exists_without_a_recorded_key_is_adopted(self):
		memory.load_seeds(V1)
		frappe.db.delete("DefaultValue", {"parent": "__default", "defkey": memory.APPLIED_DEFAULT})  # a run that died before recording
		self.assertEqual(memory.load_seeds(V1), {"inserted": 0, "already_present": 3})
		self.assertEqual(frappe.db.count("MCP Memory"), 3)

	def test_applied_keys_are_kept_as_a_site_default(self):
		memory.load_seeds(V1)
		raw = frappe.db.get_value("DefaultValue", {"parent": "__default", "defkey": memory.APPLIED_DEFAULT}, "defvalue")
		self.assertEqual(json.loads(raw), sorted(s["seed_key"] for s in json.loads(V1.read_text(encoding="utf-8"))))

	def test_bad_seed_files_are_refused_before_anything_is_inserted(self):
		good = {"seed_key": "ok-key", "topic": "pattern", "title": "t", "content": "c"}
		bad = [
			"not a list", [{"seed_key": "a"}], [{**good, "extra": 1}], [{**good, "seed_key": "Bad Key"}],
			[good, good], [{**good, "topic": "nope"}], [{**good, "title": "  "}],
			[{**good, "content": "c" * (memory.MAX_CONTENT_CHARS + 1)}],
		]
		for seeds in bad:
			with tempfile.TemporaryDirectory() as tmp:
				path = Path(tmp) / "seed.json"
				path.write_text(json.dumps(seeds), encoding="utf-8")
				with self.assertRaises(ValueError, msg=str(seeds)[:40]):
					memory.load_seeds(path)
		self.assertEqual(frappe.db.count("MCP Memory"), 0)

	def test_the_shipped_seed_file_is_valid(self):
		memory.check_seeds(json.loads(memory.SEED_PATH.read_text(encoding="utf-8")))


class TestSeedLoadsOnce(FrappeTestCase):
	"""RQ forks a work horse per job, so a bulk load must embed everything inside one process."""

	def setUp(self):
		forget_seeds()

	def test_loading_the_seed_builds_one_embedding_session(self):
		with counted_sessions() as built:
			memory.load_seeds(V1)
		self.assertEqual(len(built), 1)


class TestInstallHooks(FrappeTestCase):
	def test_the_hooks_are_registered(self):
		self.assertIn("erpsmith.install.after_install", frappe.get_hooks("after_install"))
		self.assertIn("erpsmith.install.after_migrate", frappe.get_hooks("after_migrate"))

	def test_both_hooks_load_the_seed(self):
		for hook in (install.after_install, install.after_migrate):
			with patch.object(memory, "load_seeds") as load:
				hook()
			load.assert_called_once_with()

	def test_a_failing_seed_load_does_not_stop_an_install(self):
		with patch.object(memory, "load_seeds", side_effect=RuntimeError("boom")), \
				patch.object(install.frappe, "log_error") as log:
			install.after_install()
		log.assert_called_once()
