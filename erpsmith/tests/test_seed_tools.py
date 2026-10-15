import json
import subprocess
import sys
import tempfile
from pathlib import Path

from frappe.tests.utils import FrappeTestCase

from erpsmith import memory
from erpsmith.tests import seed_rules

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / "fixtures" / "candidate_notes.json"
SCRIPT = ROOT / "dev" / "seed_candidates.py"


class TestSeedRules(FrappeTestCase):
	def test_clean_facts_pass(self):
		for text in (
			"Server scripts run in RestrictedPython; imports raise ImportError.",
			"frappe.get_roles is blocked; query Has Role instead. Frappe version v15.103.2 changed this.",
			"See https://docs.frappe.io/framework/user/en/api/server-calls and https://www.erpnext.com/x",
			"Frappe ensures each save runs validate first.",  # 'ensures' is not 'ensure'
		):
			self.assertEqual(seed_rules.hard_violations(text), [], text)

	def test_directives_are_caught(self):
		for text in ("Always use get_all", "never import", "You must call save", "Do not use f-strings",
				"don't insert rows", "Stop after 3 attempts", "MANDATORY first step", "Make sure to commit", "ensure the flag"):
			self.assertIn("directive", seed_rules.hard_violations(text), text)

	def test_identifiers_are_caught(self):
		cases = {
			"customer-name": "Built for " + "Ed" + "ify" + " ops",
			"email": "write to ops@example.com",
			"gstin": "GSTIN 29ABCDE1234F1Z5 is registered",
			"long-number": "account 1234567890123",
			"ip-address": "host 10.0.0.12 refused",
			"url": "see https://vendor-example.test/v2 for details",
		}
		for rule, text in cases.items():
			self.assertIn(rule, seed_rules.hard_violations(text), rule)
		self.assertIn("blocklisted-term", seed_rules.hard_violations("the Acme warehouse", extra_terms=["acme"]))
		self.assertTrue(seed_rules.IDENTIFIER_RULES >= set(cases) | {"blocklisted-term"})

	def test_soft_flags(self):
		self.assertIn("rupee-amount", seed_rules.soft_flags("fee of ₹50,000", "pattern", 20))
		self.assertIn("dated", seed_rules.soft_flags("2026-05-13 fix", "pattern", 20))
		self.assertIn("names-the-mcp-tooling", seed_rules.soft_flags("the MCP sanitizer blocks f-strings", "pattern", 20))
		self.assertIn("topic-context", seed_rules.soft_flags("x", "context", 20))
		self.assertIn("long", seed_rules.soft_flags("x", "pattern", seed_rules.LONG_CONTENT + 1))
		self.assertEqual(seed_rules.soft_flags("plain fact", "pattern", 20), [])


class TestSeedCandidatesScript(FrappeTestCase):
	def run_script(self, notes, blocklist=""):
		"""Run the script on `notes` with its own blocklist file, so the owner's real private/blocklist.txt
		can never change a test. Returns (completed process, candidates, report) or (process, None, None)."""
		with tempfile.TemporaryDirectory() as tmp:
			source, out, terms = Path(tmp) / "notes.json", Path(tmp) / "out", Path(tmp) / "blocklist.txt"
			source.write_text(json.dumps(notes), encoding="utf-8")
			terms.write_text(blocklist, encoding="utf-8")
			done = subprocess.run([sys.executable, str(SCRIPT), "--input", str(source), "--out", str(out),
				"--blocklist", str(terms)], capture_output=True, text=True, timeout=120)
			if done.returncode:
				return done, None, None
			return done, json.loads((out / "seed-candidates.json").read_text(encoding="utf-8")), \
				(out / "seed-candidates.md").read_text(encoding="utf-8")

	def test_candidates_are_screened_and_nothing_is_approved(self):
		notes = json.loads(FIXTURE.read_text(encoding="utf-8"))
		notes.append({"id": "pattern__named__1", "topic": "pattern", "title": "Customer note",
			"content": "Used by " + "Ed" + "ify" + " warehouses.", "created_at": "2026-01-09T00:00:00.000Z"})
		done, candidates, report = self.run_script(notes)
		self.assertEqual(done.returncode, 0, done.stderr)
		by_source = {c["source_id"]: c for c in candidates}
		self.assertEqual(by_source["sandbox-rule__clean-fact__1"]["suggested"], "include")
		self.assertEqual(by_source["pattern__directive-wording__1"]["suggested"], "review")
		self.assertIn("directive", by_source["pattern__directive-wording__1"]["flags"])
		self.assertEqual(by_source["bug-fix__dated__1"]["suggested"], "review")
		self.assertEqual(by_source["pattern__docs-link__1"]["suggested"], "include")
		for source in ("bug-fix__has-email__1", "bug-fix__foreign-url__1", "context__site-fact__1", "pattern__named__1"):
			self.assertEqual(by_source[source]["suggested"], "exclude", source)
		self.assertFalse(any(c["approved"] for c in candidates))
		self.assertEqual(set(candidates[0]), {"seed_key", "source_id", "topic", "title", "content", "flags", "suggested", "approved"})

	def test_keys_are_unique_and_valid_for_the_loader(self):
		_, candidates, _ = self.run_script(json.loads(FIXTURE.read_text(encoding="utf-8")))
		keys = [c["seed_key"] for c in candidates]
		self.assertEqual(len(keys), len(set(keys)))
		memory.check_seeds([{k: c[k] for k in ("seed_key", "topic", "title", "content")} for c in candidates])

	def test_the_report_groups_by_suggestion_and_counts(self):
		_, candidates, report = self.run_script(json.loads(FIXTURE.read_text(encoding="utf-8")))
		self.assertLess(report.index("## include"), report.index("## review"))
		self.assertLess(report.index("## review"), report.index("## exclude"))
		self.assertIn(f"{len(candidates)} notes", report)
		self.assertIn("seed-candidates.json", report)

	def test_a_blocklist_term_excludes_a_note(self):
		_, candidates, _ = self.run_script(json.loads(FIXTURE.read_text(encoding="utf-8")),
			blocklist="# customer terms\nrestrictedpython\n")
		clean = next(c for c in candidates if c["source_id"] == "sandbox-rule__clean-fact__1")
		self.assertEqual(clean["suggested"], "exclude")
		self.assertIn("blocklisted-term", clean["flags"])

	def test_bad_input_fails_with_a_message(self):
		done, _, _ = self.run_script({"not": "a list"})
		self.assertNotEqual(done.returncode, 0)
		self.assertIn("list", done.stderr)
		done = subprocess.run([sys.executable, str(SCRIPT), "--input", "/no/such.json", "--out", "/tmp/x"],
			capture_output=True, text=True, timeout=60)
		self.assertNotEqual(done.returncode, 0)
		self.assertIn("/no/such.json", done.stderr)
