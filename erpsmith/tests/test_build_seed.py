import json
import subprocess
import sys
import tempfile
from pathlib import Path

from frappe.tests.utils import FrappeTestCase

from erpsmith import memory

SCRIPT = Path(__file__).resolve().parents[2] / "dev" / "build_seed.py"


def candidate(key, topic="pattern", approved=True, **values):
	return {"seed_key": key, "source_id": f"x__{key}__1", "topic": topic, "title": f"Title {key}",
		"content": "Frappe saves a child row together with its parent.", "flags": [], "suggested": "include",
		"approved": approved, **values}


class TestBuildSeed(FrappeTestCase):
	def run_script(self, candidates, blocklist=None):
		"""Runs the script with its own empty blocklist, so the owner's private/blocklist.txt cannot change a test."""
		with tempfile.TemporaryDirectory() as tmp:
			source, out, terms = Path(tmp) / "candidates.json", Path(tmp) / "seed.json", Path(tmp) / "blocklist.txt"
			source.write_text(json.dumps(candidates), encoding="utf-8")
			terms.write_text("", encoding="utf-8")
			done = subprocess.run([sys.executable, str(SCRIPT), "--input", str(source), "--out", str(out),
				"--blocklist", blocklist or str(terms)], capture_output=True, text=True, timeout=120)
			return done, json.loads(out.read_text(encoding="utf-8")) if out.exists() else None

	def test_only_approved_notes_are_written_in_the_seed_shape(self):
		done, seeds = self.run_script([candidate("b-key", "pattern"), candidate("a-key", "sandbox-rule"),
			candidate("skipped", approved=False)])
		self.assertEqual(done.returncode, 0, done.stderr)
		self.assertEqual([s["seed_key"] for s in seeds], ["a-key", "b-key"])  # topic order first: sandbox-rule, pattern
		self.assertEqual(set(seeds[0]), {"seed_key", "topic", "title", "content"})
		memory.check_seeds(seeds)

	def test_an_approved_note_that_breaks_a_hard_rule_stops_the_build(self):
		for text in ("Always call save.", "Contact ops@example.com", "See https://vendor-example.test/x",
				"Built for " + "Ed" + "ify" + "."):
			done, seeds = self.run_script([candidate("ok"), candidate("bad", content=text)])
			self.assertNotEqual(done.returncode, 0, text)
			self.assertIn("bad", done.stderr)
			self.assertIsNone(seeds, text)  # nothing is written when any note fails

	def test_nothing_approved_is_an_error(self):
		done, seeds = self.run_script([candidate("x", approved=False)])
		self.assertNotEqual(done.returncode, 0)
		self.assertIn("approved", done.stderr)

	def test_a_missing_blocklist_given_explicitly_is_an_error(self):
		"""A typo in --blocklist would otherwise build with no customer terms checked at all."""
		missing = str(Path(tempfile.gettempdir()) / "no-such-dir-erpsmith" / "blocklist.txt")
		done, seeds = self.run_script([candidate("ok")], blocklist=missing)
		self.assertNotEqual(done.returncode, 0)
		self.assertIn(missing, done.stderr)
		self.assertIsNone(seeds)
