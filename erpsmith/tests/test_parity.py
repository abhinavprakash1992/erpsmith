import json
import sys
import unittest
from pathlib import Path

from frappe.tests.utils import FrappeTestCase

from erpsmith import memory
from erpsmith.tests.helpers import empty_memory_table

PRIVATE = Path(__file__).resolve().parents[2] / "private"
EXPORT, REFERENCE = PRIVATE / "node-memory.json", PRIVATE / "node-reference-top3.json"
MIN_MARGIN = 0.02  # a query whose Node #3 and #4 results are closer than this flips on numerical noise alone


@unittest.skipUnless(EXPORT.exists() and REFERENCE.exists(),
	"needs the private Node export: node dev/export-node-memory.mjs <mcp dir> dev/parity-queries.json private")
class TestMemoryParity(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		empty_memory_table()  # seeded notes would compete with the imported ones
		out = memory.import_json(str(EXPORT))
		assert not out["rejected"], out["rejected"]
		cls.reference = json.loads(REFERENCE.read_text(encoding="utf-8"))

	def test_there_are_ten_reference_queries_and_none_is_tie_prone(self):
		self.assertEqual(len(self.reference), 10)
		for ref in self.reference:
			self.assertIsNotNone(ref["margin"], ref["query"])
			self.assertGreaterEqual(ref["margin"], MIN_MARGIN, f"tie-prone query, pick another: {ref['query']}")

	def test_the_top_3_matches_the_node_server(self):
		report, wrong = [], []
		for ref in self.reference:
			got = memory.search_memory(ref["query"], topK=3)["results"]
			want = ref["top3"]
			same_order = [r["id"] for r in got] == [w["id"] for w in want]
			same_set = {r["id"] for r in got} == {w["id"] for w in want}
			delta = max((abs(r["score"] - w["score"]) for r, w in zip(got, want)), default=float("inf"))
			report.append(f"{'SAME' if same_order else ('SET ' if same_set else 'DIFF')}  max score diff {delta:.4f}  {ref['query']}")
			if not same_order:
				wrong.append(ref["query"])
		print("\n" + "\n".join(report), file=sys.stderr)  # the table that goes into docs/plan-2-results.md
		self.assertEqual(wrong, [], "the top 3 differs from the Node server for these queries")
