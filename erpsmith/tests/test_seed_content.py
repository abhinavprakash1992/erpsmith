import json

from frappe.tests.utils import FrappeTestCase

from erpsmith import memory
from erpsmith.tests import seed_rules
from erpsmith.tests.helpers import forget_seeds


class TestShippedSeed(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.seeds = json.loads(memory.SEED_PATH.read_text(encoding="utf-8"))

	def test_the_file_is_valid_and_not_tiny(self):
		memory.check_seeds(self.seeds)
		self.assertGreaterEqual(len(self.seeds), 10, "the spec asks for about 45 curated notes")

	def test_notes_state_facts_and_carry_nothing_customer_specific(self):
		blocklist = seed_rules.private_blocklist()  # the owner's terms; absent in CI, where the public rules still apply
		for seed in self.seeds:
			found = seed_rules.hard_violations(f"{seed['title']}\n{seed['content']}", blocklist)
			self.assertEqual(found, [], seed["seed_key"])

	def test_the_loader_installs_every_shipped_note(self):
		forget_seeds()
		self.assertEqual(memory.load_seeds(), {"inserted": len(self.seeds), "already_present": 0})

	def test_the_import_restriction_comes_back_first_on_a_fresh_install(self):
		"""Spec success criterion 4."""
		forget_seeds()
		memory.load_seeds()
		top = memory.search_memory("can I import json in a server script")["results"][0]
		self.assertEqual(top["topic"], "sandbox-rule")
		self.assertEqual(top["id"], "seed__imports-raise-importerror-in-server-scripts-frappe")
