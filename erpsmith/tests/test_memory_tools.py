import json
import os
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from erpsmith import guards, mcp, memory, vectors
from erpsmith.tests.helpers import call, empty_memory_table, make_user

NOTES = [
	("sandbox-rule", "RULE 1 — NO import statements", "Every import throws ImportError. frappe, json and datetime are already globals."),
	("pattern", "Outgoing HTTP requests", "Use frappe.make_get_request or make_post_request; requests is not importable."),
	("bug-fix", "Warehouse naming convention", "Stores are named with the city code, then Stores."),
]
TOOLS = ("search_memory", "add_memory", "update_memory", "delete_memory", "list_memory", "dump_memory")


def add_all():
	ids = []
	for topic, title, content in NOTES:
		err, out = call("add_memory", {"topic": topic, "title": title, "content": content})
		assert not err, out
		ids.append(out["id"])
	return ids


class TestMemoryTools(FrappeTestCase):
	def setUp(self):
		empty_memory_table()

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_add_search_update_list_delete(self):
		# no plan was submitted: memory tools are not gated
		err, added = call("add_memory", {"topic": "pattern", "title": "Outgoing HTTP requests", "content": "Use make_get_request."})
		self.assertFalse(err, added)
		self.assertEqual(set(added), {"ok", "action", "id", "topic", "title"})
		self.assertEqual((added["ok"], added["action"], added["topic"]), (True, "added", "pattern"))

		err, found = call("search_memory", {"query": "send an HTTP request"})
		self.assertEqual((found["ok"], found["count"], found["query"]), (True, 1, "send an HTTP request"))
		self.assertEqual(set(found["results"][0]), {"id", "topic", "title", "content", "score"})
		self.assertEqual(found["results"][0]["id"], added["id"])

		err, updated = call("update_memory", {"id": added["id"], "content": "Warehouses are named by city."})
		self.assertEqual(updated, {"ok": True, "action": "updated", "id": added["id"]})
		_, found = call("search_memory", {"query": "how are warehouses named"})
		self.assertEqual(found["results"][0]["content"], "Warehouses are named by city.")
		self.assertEqual(found["results"][0]["title"], "Outgoing HTTP requests")  # title was not sent: kept

		_, listing = call("list_memory", {})
		self.assertEqual([e["id"] for e in listing["entries"]], [added["id"]])

		err, deleted = call("delete_memory", {"id": added["id"]})
		self.assertEqual(deleted, {"ok": True, "action": "deleted", "id": added["id"]})
		_, found = call("search_memory", {"query": "how are warehouses named"})
		self.assertEqual(found["results"], [])

	def test_search_ranks_by_meaning_and_filters_by_topic(self):
		add_all()
		_, found = call("search_memory", {"query": "can I import json in a server script"})
		self.assertEqual(found["results"][0]["title"], "RULE 1 — NO import statements")
		scores = [r["score"] for r in found["results"]]
		self.assertEqual(scores, sorted(scores, reverse=True))
		self.assertTrue(all(isinstance(s, float) and -1 <= s <= 1 and s == round(s, 4) for s in scores))
		_, found = call("search_memory", {"query": "can I import json in a server script", "topic": "bug-fix"})
		self.assertEqual([r["topic"] for r in found["results"]], ["bug-fix"])

	def test_topk_is_clamped_and_bad_values_are_refused(self):
		add_all()
		for given, want in ((None, 3), (0, 1), (-1, 1), (2.5, 2), ("2", 2), (1e9, 3)):
			err, out = call("search_memory", {"query": "import", "topK": given})
			self.assertFalse(err, given)
			self.assertEqual(out["count"], want, given)
		self.assertEqual(memory.clamp("topK", 1e9, 5, 1, 20), 20)
		self.assertEqual(memory.clamp("limit", None, 20, 1, 100), 20)
		for bad in (True, "abc", [], float("nan")):
			err, out = call("search_memory", {"query": "import", "topK": bad})
			self.assertTrue(err, bad)
			self.assertEqual(out["type"], "InvalidArguments", bad)

	def test_missing_and_invalid_arguments_get_a_useful_error(self):
		cases = [
			("search_memory", {"query": ""}), ("search_memory", {"query": 5}),
			("search_memory", {"query": "x", "topic": "nope"}),
			("add_memory", {"topic": "nope", "title": "t", "content": "c"}),
			("add_memory", {"topic": "pattern", "title": "   ", "content": "c"}),
			("add_memory", {"topic": "pattern", "title": "t", "content": 5}),
			("update_memory", {"id": ""}), ("list_memory", {"topic": "nope"}),
		]
		for name, args in cases:
			err, out = call(name, args)
			self.assertTrue(err, (name, args))
			self.assertEqual(out["type"], "InvalidArguments", (name, args))
			self.assertIn("input schema", out["suggestion"])
			self.assertNotIn("call_method", out["suggestion"])
		_, out = call("search_memory", {"query": "x", "topic": "nope"})
		self.assertIn('Invalid topic "nope"', out["message"])
		self.assertIn("sandbox-rule", out["message"])
		err, out = call("search_memory", {})  # a missing required argument
		self.assertTrue(err)
		self.assertEqual(out["type"], "TypeError")

	def test_update_and_delete_of_an_unknown_id_are_errors(self):
		for name, args in (("update_memory", {"id": "nope", "content": "x"}), ("delete_memory", {"id": "nope"})):
			err, out = call(name, args)
			self.assertTrue(err, name)
			self.assertEqual(out["type"], "DoesNotExistError", name)
			self.assertIn('No memory with id "nope"', out["message"], name)

	def test_list_memory_orders_newest_first_and_counts(self):
		ids = add_all()
		for i, name in enumerate(ids):
			frappe.db.set_value("MCP Memory", name, "creation", f"2026-01-0{i + 1} 10:00:00", update_modified=False)
		_, out = call("list_memory", {})
		self.assertEqual([e["id"] for e in out["entries"]], ids[::-1])
		self.assertEqual((out["ok"], out["count"], out["total"]), (True, 3, 3))
		self.assertEqual(set(out["entries"][0]), {"id", "topic", "title", "created_at"})
		self.assertTrue(out["entries"][0]["created_at"].startswith("2026-01-03T10:00:00"))
		_, out = call("list_memory", {"limit": 2})
		self.assertEqual((out["count"], out["total"]), (2, 3))
		_, out = call("list_memory", {"topic": "pattern"})
		self.assertEqual([e["topic"] for e in out["entries"]], ["pattern"])

	def test_dump_memory_returns_markdown_and_writes_nothing(self):
		ids = add_all()
		err, out = call("dump_memory", {"path": "should-not-exist.md"})
		self.assertFalse(err, out)
		self.assertEqual(set(out), {"ok", "action", "entries", "markdown"})
		self.assertEqual((out["action"], out["entries"]), ("dumped", 3))
		md = out["markdown"]
		self.assertLess(md.index("## sandbox-rule"), md.index("## pattern"))
		self.assertLess(md.index("## pattern"), md.index("## bug-fix"))
		self.assertIn("### RULE 1 — NO import statements", md)
		self.assertIn(f"_id: `{ids[0]}`", md)
		self.assertFalse(os.path.exists("should-not-exist.md"))

	def test_non_ascii_round_trip(self):
		title = "Ünïcödé \"quotes\" ' ₹50,000 🚚"
		content = "Backticks `x` and a tab\there — fine"
		err, added = call("add_memory", {"topic": "context", "title": title, "content": content})
		self.assertFalse(err, added)
		self.assertEqual(added["title"], title)
		_, found = call("search_memory", {"query": "rupee amount with a truck emoji"})
		self.assertEqual((found["results"][0]["title"], found["results"][0]["content"]), (title, content))
		_, out = call("dump_memory")
		self.assertIn(title, out["markdown"])
		_, listing = call("list_memory", {})
		self.assertEqual(listing["entries"][0]["title"], title)

	def test_oversize_input_never_reaches_the_embedder(self):
		with patch("erpsmith.vectors.embed") as embed:
			err, out = call("add_memory", {"topic": "pattern", "title": "big", "content": "x" * 1_100_000})
			self.assertTrue(err)
			self.assertEqual(out["type"], "ValidationError")
			embed.assert_not_called()
		seen, real = [], vectors.embed
		with patch("erpsmith.vectors.embed", side_effect=lambda text: (seen.append(len(text)), real(text))[1]):
			err, out = call("search_memory", {"query": "q" * 1_100_000})
		self.assertFalse(err)
		self.assertEqual(seen, [memory.MAX_QUERY_CHARS])
		self.assertLessEqual(len(out["query"]), memory.MAX_QUERY_CHARS)

	def test_search_sees_writes_and_not_rolled_back_ones(self):
		schema = {"name": "memory_boom_test", "title": "Boom", "description": "test only",
			"inputSchema": {"type": "object", "properties": {}}}
		call("search_memory", {"query": "ghost"})  # warm this worker's cache before the failing call
		with patch.dict(mcp.SCHEMAS, {"memory_boom_test": schema}), patch.dict(mcp.IMPLS, {}):
			def boom():
				memory.add_memory("pattern", "Ghost note", "written, then the tool fails")
				raise frappe.ValidationError("boom")

			mcp.tool("memory_boom_test")(boom)
			err, _ = call("memory_boom_test")
		self.assertTrue(err)
		_, found = call("search_memory", {"query": "Ghost note written then the tool fails"})
		self.assertNotIn("Ghost note", [r["title"] for r in found["results"]])
		call("add_memory", {"topic": "pattern", "title": "Real note", "content": "written and kept"})
		_, found = call("search_memory", {"query": "Real note written and kept"})
		self.assertEqual(found["results"][0]["title"], "Real note")

	def test_non_manager_is_refused_by_every_memory_tool(self):
		ids = add_all()
		calls = {
			"search_memory": {"query": "import"},
			"add_memory": {"topic": "pattern", "title": "x", "content": "y"},
			"update_memory": {"id": ids[0], "content": "z"},
			"delete_memory": {"id": ids[0]},
			"list_memory": {},
			"dump_memory": {},
		}
		frappe.set_user(make_user("mcp-memory-nobody@example.com"))
		for name, args in calls.items():
			err, out = call(name, args)
			self.assertTrue(err, name)
			self.assertEqual(out["type"], "PermissionError", name)
			self.assertNotIn("RULE 1", json.dumps(out), name)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("MCP Memory", ids[0], "content"), NOTES[0][2])  # nothing changed

	def test_memory_tools_are_never_gated(self):
		for name in TOOLS:
			self.assertFalse(guards.is_mutating(name, {}), name)

	def test_annotations_and_schemas(self):
		for name in ("search_memory", "list_memory", "dump_memory"):
			self.assertEqual(mcp.annotations(name)["readOnlyHint"], True, name)
		self.assertEqual(mcp.annotations("add_memory")["destructiveHint"], False)
		for name in ("update_memory", "delete_memory"):
			self.assertEqual(mcp.annotations(name)["destructiveHint"], True, name)
		schemas = {name: mcp.SCHEMAS[name]["inputSchema"] for name in TOOLS}
		self.assertIn("topK", schemas["search_memory"]["properties"])
		self.assertEqual(schemas["search_memory"]["required"], ["query"])
		self.assertEqual(schemas["add_memory"]["required"], ["topic", "title", "content"])
		self.assertEqual(schemas["update_memory"]["required"], ["id"])
		self.assertIn("limit", schemas["list_memory"]["properties"])
		self.assertNotIn("path", schemas["dump_memory"]["properties"])
		for name in ("search_memory", "add_memory", "list_memory"):
			self.assertEqual(schemas[name]["properties"]["topic"]["enum"], list(memory.TOPICS), name)
