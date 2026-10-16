import frappe
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from erpsmith import guards, knowledge, mcp, vector_cache, vectors
from erpsmith.tests.helpers import call, empty_knowledge_table, knowledge_chunk, make_user

CHUNKS = [
	knowledge_chunk("doctype::Server Script", title="Server Script", summary=(
		"Server Script: Python code that runs on a document event such as before save or on a schedule. "
		"Fields: Script Type, Reference Document Type, DocType Event, Script."), content="DocType: Server Script\nFields: ..."),
	knowledge_chunk("doctype::ToDo", title="ToDo",
		summary="ToDo: a task assigned to a user, with a due date, a priority and a status.", content="DocType: ToDo"),
	knowledge_chunk("manual::pi::0", title="Perpetual Inventory", summary=(
		"# Perpetual Inventory\n\nWith perpetual inventory every stock movement posts general ledger entries automatically."),
		content="# Perpetual Inventory\n\nWith perpetual inventory every stock movement posts general ledger entries.",
		source="https://docs.erpnext.com/docs/user/manual/en/perpetual-inventory"),
]


class TestKnowledgeSearch(FrappeTestCase):
	def setUp(self):
		empty_knowledge_table()
		knowledge.sync(CHUNKS, ("doctype", "manual"))

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_results_have_the_documented_shape_and_rank_by_meaning(self):
		err, out = call("search_erpnext_knowledge", {"query": "python script that runs on document save"})
		self.assertFalse(err, out)
		self.assertEqual((out["ok"], out["count"], out["query"]), (True, 3, "python script that runs on document save"))
		self.assertEqual(out["results"][0]["title"], "Server Script")
		self.assertEqual(set(out["results"][0]), {"kind", "title", "source", "content", "score"})
		self.assertEqual(out["results"][0]["kind"], "doctype")
		scores = [r["score"] for r in out["results"]]
		self.assertEqual(scores, sorted(scores, reverse=True))
		self.assertNotIn("note", out)

	def test_kind_filters_and_doc_means_manual(self):
		for kind, want in (("doctype", {"doctype"}), ("manual", {"manual"}), ("doc", {"manual"})):
			_, out = call("search_erpnext_knowledge", {"query": "stock movements", "kind": kind})
			self.assertEqual({r["kind"] for r in out["results"]}, want, kind)
		err, out = call("search_erpnext_knowledge", {"query": "x", "kind": "nope"})
		self.assertTrue(err)
		self.assertEqual(out["type"], "InvalidArguments")
		self.assertIn("input schema", out["suggestion"])

	def test_topk_is_clamped_and_bad_values_are_refused(self):
		for given, want in ((1, 1), (0, 1), (-3, 1), (2.9, 2), ("2", 2), (None, 3), (1e9, 3)):
			_, out = call("search_erpnext_knowledge", {"query": "script", "topK": given})
			self.assertEqual(out["count"], want, given)
		for bad in (True, "abc", []):
			err, out = call("search_erpnext_knowledge", {"query": "script", "topK": bad})
			self.assertTrue(err, bad)
			self.assertEqual(out["type"], "InvalidArguments", bad)
		for bad in ("", "  ", 5, None):
			err, out = call("search_erpnext_knowledge", {"query": bad})
			self.assertTrue(err, bad)

	def test_long_content_is_cut(self):
		knowledge.sync([knowledge_chunk("doctype::Big", title="Big", summary="a very large record", content="x" * 7000)], ("doctype",))
		_, out = call("search_erpnext_knowledge", {"query": "a very large record", "topK": 1})
		content = out["results"][0]["content"]
		self.assertTrue(content.endswith("[truncated at 6000 characters]"))
		self.assertLess(len(content), 6100)

	def test_the_query_is_cut_before_it_is_embedded(self):
		seen, real = [], vectors.embed
		with patch("erpsmith.vectors.embed", side_effect=lambda text: (seen.append(len(text)), real(text))[1]):
			err, _ = call("search_erpnext_knowledge", {"query": "q" * 1_100_000})
		self.assertFalse(err)
		self.assertEqual(seen, [4000])

	def test_an_empty_index_says_how_to_build_it(self):
		empty_knowledge_table()
		_, out = call("search_erpnext_knowledge", {"query": "anything"})
		self.assertEqual((out["count"], out["results"]), (0, []))
		self.assertIn("erpsmith.knowledge.build", out["note"])

	def test_a_sync_is_visible_to_the_next_search(self):
		_, before = call("search_erpnext_knowledge", {"query": "warehouse transfer between stores", "topK": 1})
		knowledge.sync([knowledge_chunk("manual::wt::0", title="Warehouse Transfer",
			summary="# Warehouse Transfer\n\nMove stock between stores with a warehouse transfer.", content="x")], ("manual",))
		_, after = call("search_erpnext_knowledge", {"query": "warehouse transfer between stores", "topK": 1})
		self.assertNotEqual(before["results"][0]["title"], "Warehouse Transfer")
		self.assertEqual(after["results"][0]["title"], "Warehouse Transfer")

	def test_non_manager_is_refused(self):
		frappe.set_user(make_user("mcp-memory-nobody@example.com"))
		err, out = call("search_erpnext_knowledge", {"query": "script"})
		self.assertTrue(err)
		self.assertEqual(out["type"], "PermissionError")
		self.assertNotIn("Server Script", str(out))

	def test_annotations_schema_and_gate(self):
		self.assertEqual(mcp.annotations("search_erpnext_knowledge")["readOnlyHint"], True)
		self.assertFalse(guards.is_mutating("search_erpnext_knowledge", {}))
		schema = mcp.SCHEMAS["search_erpnext_knowledge"]["inputSchema"]
		self.assertEqual(schema["required"], ["query"])
		self.assertIn("topK", schema["properties"])
		self.assertEqual(schema["properties"]["kind"]["enum"], ["doctype", "manual", "doc", "customization"])

	def test_rule_0_names_both_tools_and_the_role(self):
		for text in ("search_memory", "search_erpnext_knowledge", "System Manager", "user manual"):
			self.assertIn(text, mcp.INSTRUCTIONS)


class TestRealDoctypeRanking(FrappeTestCase):
	"""Acceptance on real chunks from this site's own DocTypes (no network)."""

	def test_a_plain_english_question_finds_the_doctype(self):
		empty_knowledge_table()
		names = ("Server Script", "ToDo", "Role", "Email Account", "Notification")
		knowledge.sync(knowledge.doctype_chunks(only=names), ("doctype",))
		_, out = call("search_erpnext_knowledge", {"query": "python script that runs on document save", "kind": "doctype", "topK": 3})
		self.assertEqual(out["results"][0]["title"], "Server Script", [r["title"] for r in out["results"]])


EXACT = [
	knowledge_chunk("doctype::Client Script", title="Client Script", summary=(
		"Client Script: server script, script, server script, script; a script for the server script form.")),
	knowledge_chunk("doctype::Server Script", title="Server Script", summary=(
		"Server Script: Python code that runs on a document event or on a schedule.")),
	knowledge_chunk("doctype::Website Script", title="Website Script", summary="Website Script: JavaScript added to every page of the website."),
	knowledge_chunk("manual::ss::0", title="Server Scripts", summary="# Server Scripts\n\nA server script runs Python code on the server.",
		content="# Server Scripts\n\nA server script runs Python code on the server."),
]


class TestExactDoctypeName(FrappeTestCase):
	def setUp(self):
		empty_knowledge_table()
		knowledge.sync(EXACT, ("doctype", "manual"))

	def titles(self, **args):
		err, out = call("search_erpnext_knowledge", {"query": "server script", **args})
		self.assertFalse(err, out)
		return [r["title"] for r in out["results"]], out

	def test_an_exact_doctype_name_ranks_its_chunk_first(self):
		semantic = [row.name for row, _ in vector_cache.search(knowledge.DOCTYPE, vectors.embed("server script"), 3, ("kind",),
			{"kind": "doctype"})]
		self.assertEqual(semantic[0], "doctype::Client Script")  # without the exact-name step the wrong one wins
		titles, out = self.titles(topK=3)
		self.assertEqual(titles[0], "Server Script")
		self.assertEqual(out["results"][0]["score"], 1.0)
		self.assertEqual(out["count"], 3)
		for query in ("  SERVER SCRIPT ", "Server script"):
			_, other = call("search_erpnext_knowledge", {"query": query, "topK": 3})
			self.assertEqual(other["results"][0]["title"], "Server Script", query)

	def test_the_exact_match_is_not_listed_twice(self):
		for k in (1, 2, 4):
			titles, out = self.titles(topK=k)
			self.assertEqual(titles.count("Server Script"), 1, titles)
			self.assertEqual(len(titles), len(set(titles)))
			self.assertEqual(out["count"], k)
		self.assertEqual(self.titles(kind="doctype", topK=3)[0][0], "Server Script")

	def test_kind_manual_ignores_the_exact_match(self):
		for kind in ("manual", "doc", "customization"):
			_, out = self.titles(kind=kind)
			self.assertNotIn("doctype", {r["kind"] for r in out["results"]}, kind)
			self.assertNotIn(1.0, [r["score"] for r in out["results"]], kind)

	def test_a_query_that_is_no_doctype_name_is_unchanged(self):
		_, out = call("search_erpnext_knowledge", {"query": "python code that runs on the server", "topK": 2})
		self.assertLess(out["results"][0]["score"], 1.0)
