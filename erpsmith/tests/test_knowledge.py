from unittest.mock import patch

import frappe
import numpy as np
from frappe.tests.utils import FrappeTestCase

from erpsmith import knowledge, vector_cache, vectors
from erpsmith.tests.helpers import empty_knowledge_table, knowledge_chunk

DOCTYPE = "MCP Knowledge Chunk"


class TestKnowledgeChunkDoctype(FrappeTestCase):
	def test_fields_and_permissions(self):
		meta = frappe.get_meta(DOCTYPE)
		for name in ("kind", "title", "source", "chunk_key", "summary", "content", "content_hash", "vector"):
			self.assertTrue(meta.has_field(name), name)
		self.assertEqual(meta.get_field("kind").options.split("\n"), ["doctype", "manual"])
		self.assertEqual({p.role for p in meta.permissions}, {"System Manager"})
		for name in ("summary", "content_hash", "vector"):
			self.assertTrue(meta.get_field(name).hidden, name)
		self.assertEqual(meta.autoname, "field:chunk_key")


class TestDoctypeChunks(FrappeTestCase):
	NAMES = ("Role", "Has Role", "ToDo", "Notification Settings", "Notification Subscribed Document")

	def setUp(self):
		self.chunks = {c["title"]: c for c in knowledge.doctype_chunks(only=self.NAMES)}

	def test_one_chunk_per_requested_doctype_with_the_documented_shape(self):
		self.assertEqual(set(self.chunks), set(self.NAMES))
		chunk = self.chunks["ToDo"]
		self.assertEqual(set(chunk), {"kind", "chunk_key", "title", "source", "summary", "content"})
		self.assertEqual((chunk["kind"], chunk["chunk_key"], chunk["source"]), ("doctype", "doctype::ToDo", "/app/doctype/ToDo"))
		self.assertIn("DocType: ToDo", chunk["content"])
		self.assertIn("`tabToDo`", chunk["content"])
		self.assertIn("- allocated_to (Link→User)", chunk["content"])
		self.assertIn("ToDo", chunk["summary"])

	def test_child_tables_links_and_reverse_links(self):
		settings = self.chunks["Notification Settings"]
		self.assertIn("Child tables: subscribed_documents → Notification Subscribed Document", settings["content"])
		row = self.chunks["Notification Subscribed Document"]
		self.assertIn("child table", row["content"])
		self.assertIn("Notification Settings.subscribed_documents (Table MultiSelect)", row["content"])
		self.assertIn("Line items / rows of Notification Settings", row["summary"])
		self.assertIn("Has Role.role", self.chunks["Role"]["content"])

	def test_intros_from_the_manual_go_into_the_summary(self):
		with_intro = knowledge.doctype_chunks(intros={"Role": "A Role is a set of permissions."}, only=("Role",))[0]
		self.assertIn("A Role is a set of permissions.", with_intro["summary"])
		self.assertNotIn("A Role is a set of permissions.", with_intro["content"])

	def test_fields_defined_by_a_custom_field_are_marked(self):
		meta = frappe.get_meta("ToDo")
		fields = [f for f in meta.fields if f.fieldtype not in knowledge.LAYOUT]
		chunk = knowledge._doctype_chunk(meta, fields, "frappe", {}, {}, {"ToDo": {"priority"}}, {})
		lines = {line.split(" ")[1]: line for line in chunk["content"].splitlines() if line.startswith("- ")}
		self.assertTrue(lines["priority"].endswith("[custom]"))
		self.assertNotIn("[custom]", lines["status"])

	def test_site_customizations_show_up(self):
		self.addCleanup(self._remove_property_setter)
		frappe.make_property_setter({"doctype": "ToDo", "doctype_or_field": "DocField", "fieldname": "priority",
			"property": "label", "value": "Urgency Probe", "property_type": "Data"})
		frappe.clear_cache(doctype="ToDo")
		chunk = knowledge.doctype_chunks(only=("ToDo",))[0]
		self.assertIn('"Urgency Probe"', chunk["content"])

	@staticmethod
	def _remove_property_setter():
		frappe.db.delete("Property Setter", {"doc_type": "ToDo", "field_name": "priority", "property": "label"})
		frappe.clear_cache(doctype="ToDo")

	def test_own_doctypes_are_left_out_and_the_sites_are_in(self):
		titles = {c["title"] for c in knowledge.doctype_chunks()}
		self.assertTrue({"ToDo", "DocType", "User"} <= titles)
		self.assertFalse({"MCP Memory", "MCP Plan", "MCP Knowledge Chunk"} & titles)

	def test_a_doctype_whose_meta_fails_is_skipped_and_logged(self):
		real = frappe.get_meta

		def flaky(name, *args, **kwargs):
			if name == "Role":
				raise RuntimeError("broken doctype")
			return real(name, *args, **kwargs)

		with patch("erpsmith.knowledge.frappe.get_meta", side_effect=flaky), \
				patch("erpsmith.knowledge.frappe.log_error") as log:
			chunks = knowledge.doctype_chunks(only=("Role", "ToDo"))
		self.assertEqual([c["title"] for c in chunks], ["ToDo"])
		log.assert_called_once()


class TestSync(FrappeTestCase):
	def setUp(self):
		empty_knowledge_table()

	def test_adds_updates_deletes_and_skips_unchanged_chunks(self):
		a = knowledge_chunk("doctype::A", summary="alpha")
		b = knowledge_chunk("doctype::B", summary="beta")
		c = knowledge_chunk("manual::x::0", summary="gamma")
		both = ("doctype", "manual")
		self.assertEqual(knowledge.sync([a, b, c], both), {"added": 3, "updated": 0, "unchanged": 0, "deleted": 0})
		with patch("erpsmith.vectors.embed", wraps=vectors.embed) as embed:
			self.assertEqual(knowledge.sync([a, b, c], both), {"added": 0, "updated": 0, "unchanged": 3, "deleted": 0})
			embed.assert_not_called()
			self.assertEqual(knowledge.sync([a, {**b, "summary": "beta changed"}, c], both)["updated"], 1)
			self.assertEqual(embed.call_count, 1)
			self.assertEqual(knowledge.sync([a, c], both)["deleted"], 1)
		self.assertFalse(frappe.db.exists(DOCTYPE, "doctype::B"))
		self.assertEqual(frappe.db.get_value(DOCTYPE, "doctype::A", ["kind", "title", "source"]), ("doctype", "doctype::A", "/x"))

	def test_kinds_outside_delete_kinds_are_left_alone(self):
		knowledge.sync([knowledge_chunk("doctype::A"), knowledge_chunk("manual::x::0")], ("doctype", "manual"))
		out = knowledge.sync([knowledge_chunk("doctype::A")], ("doctype",))
		self.assertEqual(out["deleted"], 0)
		self.assertTrue(frappe.db.exists(DOCTYPE, "manual::x::0"))

	def test_the_summary_is_what_gets_embedded(self):
		knowledge.sync([knowledge_chunk("doctype::A", summary="alpha beta", content="entirely different text")], ("doctype",))
		stored = frappe.db.get_value(DOCTYPE, "doctype::A", "vector")
		np.testing.assert_allclose(vectors.from_b64(stored), vectors.embed("alpha beta"), atol=1e-6)
		knowledge.sync([knowledge_chunk("doctype::E", summary="", content="only content")], ("doctype",))
		stored = frappe.db.get_value(DOCTYPE, "doctype::E", "vector")
		np.testing.assert_allclose(vectors.from_b64(stored), vectors.embed("only content"), atol=1e-6)

	def test_every_field_is_part_of_the_hash(self):
		for field, value in (("title", "other"), ("source", "/y"), ("summary", "other"), ("content", "other")):
			empty_knowledge_table()
			base = knowledge_chunk("doctype::A")
			knowledge.sync([base], ("doctype",))
			self.assertEqual(knowledge.sync([{**base, field: value}], ("doctype",))["updated"], 1, field)

	def test_searches_see_a_sync_at_once(self):
		query = vectors.embed("alpha")
		self.assertEqual(vector_cache.search(DOCTYPE, query, 1, ("kind",)), [])
		knowledge.sync([knowledge_chunk("doctype::A", summary="alpha")], ("doctype",))
		self.assertEqual([row.name for row, _ in vector_cache.search(DOCTYPE, query, 1, ("kind",))], ["doctype::A"])
		knowledge.sync([], ("doctype",))
		self.assertEqual(vector_cache.search(DOCTYPE, query, 1, ("kind",)), [])

	def test_an_update_moves_modified_so_the_cache_fingerprint_sees_it(self):
		chunk = knowledge_chunk("doctype::A", summary="alpha")
		knowledge.sync([chunk], ("doctype",))
		before_modified = frappe.db.get_value(DOCTYPE, "doctype::A", "modified")
		before = vector_cache._fingerprint(DOCTYPE)
		self.assertEqual(knowledge.sync([{**chunk, "summary": "alpha changed"}], ("doctype",))["updated"], 1)
		self.assertGreater(frappe.db.get_value(DOCTYPE, "doctype::A", "modified"), before_modified)
		self.assertNotEqual(vector_cache._fingerprint(DOCTYPE), before)
