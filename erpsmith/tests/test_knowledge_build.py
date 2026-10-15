import json
from unittest.mock import MagicMock, patch

import frappe
import requests
from frappe.tests.utils import FrappeTestCase

from erpsmith import install, knowledge, memory
from erpsmith.tests.helpers import counted_sessions, empty_knowledge_table, knowledge_chunk

DOCTYPE = "MCP Knowledge Chunk"


def paragraphs(n):
	return "\n\n".join(f"Paragraph {i}. " + "word " * 60 for i in range(n))


PAGES = [
	{"title": "Role\n", "route": "docs/user/manual/en/role",
		"content": "A Role is a set of permissions that you give to users, and you can combine several roles for one user.\n\n"
			"![diagram](/files/role.png)\n\n## Setting up\n\nOpen the <b>Role</b> list and add a role with a short name that "
			"describes the job it does for the team.\n\n## Short\n\ntiny"},
	{"title": "Long Page", "route": "docs/user/manual/en/long-page", "content": paragraphs(12)},
	{"title": "Empty Page", "route": "docs/user/manual/en/empty-page", "content": None},
]


def reply(payload=None, status=200, bad_json=False):
	response = MagicMock()
	response.raise_for_status.side_effect = requests.HTTPError(str(status)) if status >= 400 else None
	response.json.side_effect = ValueError("not json") if bad_json else None
	response.json.return_value = payload
	return response


class TestManualChunks(FrappeTestCase):
	def test_pages_are_cleaned_split_at_h2_and_short_parts_dropped(self):
		role = [c for c in knowledge.manual_chunks(PAGES) if c["title"] == "Role"]
		self.assertEqual(len(role), 2)
		self.assertTrue(role[0]["content"].startswith("# Role\n\nA Role is a set of permissions"))
		self.assertTrue(role[1]["content"].startswith("# Role\n\n## Setting up"))
		for chunk in role:
			self.assertNotIn("![", chunk["content"])
			self.assertNotIn("<b>", chunk["content"])
		self.assertEqual(role[0]["source"], "https://docs.erpnext.com/docs/user/manual/en/role")
		self.assertEqual((role[0]["kind"], role[0]["summary"]), ("manual", role[0]["content"]))

	def test_long_sections_are_split_and_keys_are_stable(self):
		first = [c for c in knowledge.manual_chunks(PAGES) if c["title"] == "Long Page"]
		again = [c for c in knowledge.manual_chunks(PAGES) if c["title"] == "Long Page"]
		self.assertTrue(3 <= len(first) <= 4, len(first))
		self.assertTrue(all(len(c["content"]) <= knowledge.CHUNK_CHARS + len("# Long Page\n\n") for c in first))
		self.assertEqual([c["chunk_key"] for c in first], [c["chunk_key"] for c in again])
		self.assertRegex(first[0]["chunk_key"], r"^manual::[0-9a-f]{16}::0$")
		self.assertTrue(first[1]["chunk_key"].endswith("::1"))

	def test_pages_without_content_or_route_give_nothing(self):
		self.assertEqual([c for c in knowledge.manual_chunks(PAGES) if c["title"] == "Empty Page"], [])
		self.assertEqual(knowledge.manual_chunks([{"title": "x", "route": None, "content": paragraphs(3)}, "junk"]), [])

	def test_long_titles_are_cut_to_the_column_width(self):
		page = {"title": "T" * 300, "route": "docs/user/manual/en/t", "content": paragraphs(2)}
		self.assertTrue(all(len(c["title"]) == 140 for c in knowledge.manual_chunks([page])))

	def test_a_page_with_a_non_text_title_or_content_does_not_break_the_others(self):
		odd = [{"title": 5, "route": "docs/user/manual/en/a", "content": paragraphs(2)},
			{"title": "Number", "route": "docs/user/manual/en/b", "content": 12345}]
		self.assertEqual(knowledge.manual_chunks(PAGES + odd)[:len(knowledge.manual_chunks(PAGES))], knowledge.manual_chunks(PAGES))
		titles = [c["title"] for c in knowledge.manual_chunks(odd)]
		self.assertTrue(titles and set(titles) == {""})  # untitled chunks for the first page, nothing for the second

	def test_intros_come_from_the_first_chunk_of_each_page(self):
		intros = knowledge._intros(knowledge.manual_chunks(PAGES))
		self.assertTrue(intros["Role"].startswith("A Role is a set of permissions"))
		self.assertLessEqual(len(intros["Role"]), 500)


class TestFetchManual(FrappeTestCase):
	def test_returns_the_pages_and_asks_politely(self):
		with patch("erpsmith.knowledge.requests.get", return_value=reply({"data": PAGES})) as get:
			self.assertEqual(knowledge.fetch_manual(), PAGES)
		args, kwargs = get.call_args
		self.assertEqual(args[0], knowledge.MANUAL_URL)
		self.assertGreaterEqual(kwargs["timeout"], 60)
		self.assertEqual(json.loads(kwargs["params"]["filters"])[0], ["route", "like", "docs/user/%"])

	def test_pages_without_a_route_are_dropped(self):
		with patch("erpsmith.knowledge.requests.get", return_value=reply({"data": [PAGES[0], {"title": "no route"}, "junk"]})):
			self.assertEqual(knowledge.fetch_manual(), [PAGES[0]])

	def test_every_kind_of_failure_gives_none_and_is_logged(self):
		failures = [
			{"side_effect": requests.ConnectionError("down")},
			{"return_value": reply({"data": []}, status=500)},
			{"return_value": reply(bad_json=True)},
			{"return_value": reply({"exc": "Traceback ..."})},
			{"return_value": reply({"data": "oops"})},
			{"return_value": reply(["not", "a", "dict"])},
			{"return_value": reply({"data": []})},
			{"return_value": reply({"data": [{"title": "no route"}, {"title": "route is a number", "route": 5}]})},
		]
		for kwargs in failures:
			with patch("erpsmith.knowledge.requests.get", **kwargs), patch("erpsmith.knowledge.frappe.log_error") as log:
				self.assertIsNone(knowledge.fetch_manual(), kwargs)
			log.assert_called_once()


class TestBuild(FrappeTestCase):
	ONLY = ("Role", "Has Role", "ToDo")

	def setUp(self):
		empty_knowledge_table()

	def run_build(self, pages, **kwargs):
		with patch.object(knowledge, "fetch_manual", return_value=pages):
			return knowledge.build(commit=False, only_doctypes=self.ONLY, **kwargs)

	def test_build_adds_doctype_and_manual_chunks(self):
		manual = knowledge.manual_chunks(PAGES)
		out = self.run_build(PAGES)
		self.assertEqual((out["added"], out["manual"]), (3 + len(manual), f"{len(PAGES)} pages"))
		role = frappe.get_doc(DOCTYPE, "doctype::Role")
		self.assertIn("A Role is a set of permissions", role.summary)  # the intro of the manual page of the same name

	def test_a_second_build_embeds_nothing(self):
		self.run_build(PAGES)
		with patch("erpsmith.vectors.embed") as embed:
			out = self.run_build(PAGES)
		embed.assert_not_called()
		self.assertEqual((out["added"], out["updated"], out["deleted"]), (0, 0, 0))

	def test_a_manual_outage_keeps_the_old_chunks_and_summaries(self):
		self.run_build(PAGES)
		before = frappe.db.count(DOCTYPE, {"kind": "manual"})
		out = self.run_build(None)
		self.assertEqual(out["manual"], "skipped: could not be fetched")
		self.assertEqual((out["added"], out["updated"], out["deleted"]), (0, 0, 0))  # DocType summaries kept their intros
		self.assertEqual(frappe.db.count(DOCTYPE, {"kind": "manual"}), before)

	def test_an_empty_manual_response_keeps_the_stored_chunks(self):
		self.run_build(PAGES)
		before = frappe.db.count(DOCTYPE, {"kind": "manual"})
		with patch("erpsmith.knowledge.requests.get", return_value=reply({"data": []})), \
				patch("erpsmith.knowledge.frappe.log_error"):
			out = knowledge.build(commit=False, only_doctypes=self.ONLY)
		self.assertEqual(out["manual"], "skipped: could not be fetched")
		self.assertEqual((out["added"], out["updated"], out["deleted"]), (0, 0, 0))
		self.assertEqual(frappe.db.count(DOCTYPE, {"kind": "manual"}), before)

	def test_a_manual_that_was_not_requested_is_reported_as_such(self):
		self.assertEqual(self.run_build(None, include_manual=False)["manual"], "not requested")

	def test_a_full_build_drops_chunks_whose_source_is_gone(self):
		knowledge.sync([knowledge_chunk("doctype::Ghost"), knowledge_chunk("manual::old::0")], ("doctype", "manual"))
		with patch.object(knowledge, "doctype_chunks", return_value=[knowledge_chunk("doctype::Real")]), \
				patch.object(knowledge, "fetch_manual", return_value=PAGES):
			out = knowledge.build(commit=False)
		self.assertEqual(out["deleted"], 2)
		self.assertTrue(frappe.db.exists(DOCTYPE, "doctype::Real"))
		self.assertFalse(frappe.db.exists(DOCTYPE, "doctype::Ghost"))
		self.assertFalse(frappe.db.exists(DOCTYPE, "manual::old::0"))  # a page that left the manual

	def test_a_manual_outage_does_not_delete_manual_chunks(self):
		knowledge.sync([knowledge_chunk("doctype::Ghost"), knowledge_chunk("manual::old::0")], ("doctype", "manual"))
		with patch.object(knowledge, "doctype_chunks", return_value=[knowledge_chunk("doctype::Real")]), \
				patch.object(knowledge, "fetch_manual", return_value=None):
			out = knowledge.build(commit=False)
		self.assertEqual(out["deleted"], 1)  # only the DocType that is gone
		self.assertTrue(frappe.db.exists(DOCTYPE, "manual::old::0"))

	def test_building_named_doctypes_never_deletes_the_others(self):
		knowledge.sync([knowledge_chunk("doctype::Other")], ("doctype",))
		self.run_build(PAGES)
		self.assertTrue(frappe.db.exists(DOCTYPE, "doctype::Other"))

	def test_build_commits_in_batches_only_when_asked(self):
		with patch.object(frappe.db, "commit") as commit:
			self.run_build(PAGES)
		commit.assert_not_called()
		empty_knowledge_table()
		with patch.object(frappe.db, "commit") as commit, patch.object(knowledge, "COMMIT_EVERY", 2), \
				patch.object(knowledge, "fetch_manual", return_value=PAGES):
			knowledge.build(commit=True, only_doctypes=self.ONLY)
		self.assertGreaterEqual(commit.call_count, 3)


class TestBuildLoadsOnce(FrappeTestCase):
	"""RQ forks a work horse per job, so a build must embed all its chunks inside the one job it runs in."""

	def setUp(self):
		empty_knowledge_table()

	def test_a_build_creates_one_embedding_session(self):
		with counted_sessions() as built, patch.object(knowledge, "fetch_manual", return_value=PAGES):
			knowledge.build(commit=False, only_doctypes=("Role", "ToDo"))
		self.assertEqual(len(built), 1)


class TestEnqueue(FrappeTestCase):
	def test_the_build_is_queued_once_on_the_long_queue(self):
		with patch("erpsmith.knowledge.frappe.enqueue") as enqueue:
			knowledge.enqueue_build()
		enqueue.assert_called_once()
		args, kwargs = enqueue.call_args
		self.assertEqual(args, ("erpsmith.knowledge.build",))
		self.assertEqual((kwargs["queue"], kwargs["job_id"], kwargs["deduplicate"], kwargs["enqueue_after_commit"]),
			("long", "erpsmith-knowledge-build", True, True))
		self.assertGreaterEqual(kwargs["timeout"], 1800)

	def test_a_queue_failure_is_logged_not_raised(self):
		with patch("erpsmith.knowledge.frappe.enqueue", side_effect=ConnectionError("redis down")), \
				patch("erpsmith.knowledge.frappe.log_error") as log:
			knowledge.enqueue_build()
		log.assert_called_once()

	def test_both_install_hooks_queue_the_build(self):
		for hook in (install.after_install, install.after_migrate):
			with patch.object(memory, "load_seeds"), patch.object(knowledge, "enqueue_build") as queue:
				hook()
			queue.assert_called_once_with()
