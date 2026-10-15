from unittest.mock import patch

import frappe
import numpy as np
from frappe.tests.utils import FrappeTestCase

from erpsmith import memory, vectors
from erpsmith.tests.helpers import empty_memory_table, make_user


def note(**values):
	return frappe.get_doc({"doctype": "MCP Memory", "topic": "pattern", "title": "A title",
		"content": "A body", **values})


class TestMCPMemory(FrappeTestCase):
	def setUp(self):
		empty_memory_table()
		self.assertEqual(frappe.db.count("MCP Memory"), 0)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_insert_embeds_title_and_content(self):
		doc = note(title="Outgoing HTTP requests", content="Use frappe.make_get_request").insert()
		want = vectors.embed("Outgoing HTTP requests\n\nUse frappe.make_get_request")
		np.testing.assert_allclose(vectors.from_b64(doc.vector), want, atol=1e-6)

	def test_changing_the_text_re_embeds_and_keeps_the_name(self):
		doc = note(title="Pick me", content="first").insert()
		name, old_vector = doc.name, doc.vector
		doc.content = "completely different words about warehouses"
		doc.save()
		self.assertEqual(doc.name, name)
		self.assertNotEqual(doc.vector, old_vector)
		stored = frappe.db.get_value("MCP Memory", name, "vector")
		np.testing.assert_allclose(vectors.from_b64(stored),
			vectors.embed("Pick me\n\ncompletely different words about warehouses"), atol=1e-6)

	def test_saving_without_a_text_change_does_not_embed_again(self):
		doc = note().insert()
		with patch("erpsmith.vectors.embed") as embed:
			doc.seed_key = "something-else"  # not part of the note's text
			doc.save()
		embed.assert_not_called()

	def test_the_name_follows_the_node_id_format(self):
		doc = note(topic="sandbox-rule", title="RULE 1 — NO import statements!").insert()
		self.assertRegex(doc.name, r"^sandbox-rule__rule-1-no-import-statements__\d{14}$")

	def test_the_same_title_gets_distinct_names(self):
		names = {note(title="Same title").insert().name for _ in range(3)}
		self.assertEqual(len(names), 3)

	def test_a_name_chosen_by_the_caller_is_kept(self):
		self.assertEqual(note(name="seed__my-key").insert().name, "seed__my-key")

	def test_title_and_content_are_trimmed_and_required(self):
		doc = note(title="  padded  ", content="\n body \n").insert()
		self.assertEqual((doc.title, doc.content), ("padded", "body"))
		for values in ({"title": "   "}, {"content": "\n \t"}):
			with self.assertRaises(frappe.ValidationError, msg=str(values)):
				note(**values).insert()

	def test_topic_must_be_one_of_the_list(self):
		options = frappe.get_meta("MCP Memory").get_field("topic").options.split("\n")
		self.assertEqual(options, list(memory.TOPICS))
		with self.assertRaises(frappe.ValidationError):
			note(topic="nope").insert()

	def test_size_caps(self):
		note(title="t" * memory.MAX_TITLE_CHARS, content="c" * memory.MAX_CONTENT_CHARS).insert()  # at the caps: fine
		too_big = ({"title": "t" * (memory.MAX_TITLE_CHARS + 1)},
			{"content": "c" * (memory.MAX_CONTENT_CHARS + 1)}, {"content": "c" * 1_100_000})
		with patch("erpsmith.vectors.embed") as embed:
			for values in too_big:
				with self.assertRaises(frappe.ValidationError, msg=list(values)[0]):
					note(**values).insert()
			embed.assert_not_called()  # refused before any embedding work

	def test_only_system_manager_can_use_the_table(self):
		self.assertEqual({p.role for p in frappe.get_meta("MCP Memory").permissions}, {"System Manager"})
		doc = note().insert()
		frappe.set_user(make_user("mcp-memory-nobody@example.com"))
		self.assertFalse(frappe.has_permission("MCP Memory", "read"))
		self.assertFalse(frappe.has_permission("MCP Memory", "create"))
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("MCP Memory", doc.name).check_permission("read")

	def test_internal_fields_are_hidden_in_desk(self):
		meta = frappe.get_meta("MCP Memory")
		for fieldname in ("vector", "seed_key"):
			field = meta.get_field(fieldname)
			self.assertTrue(field.hidden and field.read_only, fieldname)
		self.assertTrue(meta.track_changes)
