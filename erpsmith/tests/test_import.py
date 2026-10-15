import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_datetime

from erpsmith import memory, vectors
from erpsmith.tests.helpers import call, counted_sessions, empty_memory_table

FIXTURE = Path(__file__).parent / "fixtures" / "notes_export.json"
ROOT = Path(__file__).resolve().parents[2]


def write(tmp, data):
	path = Path(tmp) / "export.json"
	path.write_text(json.dumps(data), encoding="utf-8")
	return path


class TestImportJson(FrappeTestCase):
	def setUp(self):
		empty_memory_table()

	def test_import_keeps_ids_dates_and_embeds_everything(self):
		self.assertEqual(memory.import_json(FIXTURE), {"imported": 3, "already_present": 0, "rejected": []})
		notes = json.loads(FIXTURE.read_text(encoding="utf-8"))
		for note in notes:
			doc = frappe.get_doc("MCP Memory", note["id"])
			self.assertEqual((doc.topic, doc.title, doc.content), (note["topic"], note["title"], note["content"]))
			self.assertEqual(len(vectors.from_b64(doc.vector)), vectors.DIM)
			self.assertEqual(get_datetime(doc.creation), memory._utc_to_site(note["created_at"]))
		_, listing = call("list_memory", {})
		self.assertEqual([e["id"] for e in listing["entries"]], [n["id"] for n in reversed(notes)])

	def test_dates_are_converted_from_utc_to_the_site_timezone(self):
		zone = ZoneInfo(frappe.utils.get_system_timezone())
		want = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc).astimezone(zone).replace(tzinfo=None)
		self.assertEqual(memory._utc_to_site("2026-01-01T10:00:00.000Z"), want)
		self.assertIsNone(memory._utc_to_site(None))

	def test_a_second_import_changes_nothing_and_never_embeds(self):
		memory.import_json(FIXTURE)
		with patch("erpsmith.vectors.embed") as embed:
			self.assertEqual(memory.import_json(FIXTURE), {"imported": 0, "already_present": 3, "rejected": []})
		embed.assert_not_called()

	def test_bad_notes_are_rejected_one_by_one(self):
		good = {"id": "x__ok__1", "topic": "pattern", "title": "fine", "content": "fine", "created_at": "2026-01-01T00:00:00Z"}
		data = [
			good,
			{"id": "x__no-title__1", "topic": "pattern", "content": "c"},
			{"id": "x__bad-topic__1", "topic": "nope", "title": "t", "content": "c"},
			{"id": "x__big__1", "topic": "pattern", "title": "t", "content": "c" * (memory.MAX_CONTENT_CHARS + 1)},
			{"id": "x__bad-date__1", "topic": "pattern", "title": "t", "content": "c", "created_at": "yesterday"},
			{"topic": "pattern", "title": "t", "content": "c"},  # no id
			"not an object",
			{**good, "id": "x__ok__2"},  # the import carries on after the failures
		]
		with tempfile.TemporaryDirectory() as tmp:
			out = memory.import_json(write(tmp, data))
		self.assertEqual((out["imported"], out["already_present"]), (2, 0))
		self.assertEqual([r["id"] for r in out["rejected"]],
			["x__no-title__1", "x__bad-topic__1", "x__big__1", "x__bad-date__1", None, None])
		self.assertTrue(all(r["reason"] for r in out["rejected"]))
		self.assertEqual(frappe.db.count("MCP Memory"), 2)  # a rejected note leaves no row behind

	def test_a_note_that_fails_after_its_insert_is_rolled_back_and_the_rest_still_import(self):
		def note(id):  # noqa: A002
			return {"id": id, "topic": "pattern", "title": id, "content": "c", "created_at": "2026-01-01T00:00:00Z"}

		real = frappe.db.set_value

		def fail_on_boom(doctype, name, *args, **kwargs):  # the call that sets `creation`, after the row is inserted
			if name == "x__boom__1":
				raise RuntimeError("disk full")
			return real(doctype, name, *args, **kwargs)

		with tempfile.TemporaryDirectory() as tmp, patch.object(frappe.db, "set_value", fail_on_boom):
			out = memory.import_json(write(tmp, [note("x__first__1"), note("x__boom__1"), note("x__last__1")]))
		self.assertEqual((out["imported"], out["already_present"]), (2, 0))
		self.assertEqual([r["id"] for r in out["rejected"]], ["x__boom__1"])
		self.assertIn("disk full", out["rejected"][0]["reason"])
		self.assertFalse(frappe.db.exists("MCP Memory", "x__boom__1"))  # its insert was rolled back
		self.assertTrue(frappe.db.exists("MCP Memory", "x__first__1"))
		self.assertTrue(frappe.db.exists("MCP Memory", "x__last__1"))  # imported after the failure

	def test_files_that_are_not_an_export_raise(self):
		with tempfile.TemporaryDirectory() as tmp:
			with self.assertRaises(ValueError):
				memory.import_json(write(tmp, {"not": "a list"}))
		with self.assertRaises(FileNotFoundError):
			memory.import_json("/no/such/export.json")


class TestImportLoadsOnce(FrappeTestCase):
	"""A bulk import runs in one process, so the embedding model loads once, not once per note."""

	def setUp(self):
		empty_memory_table()

	def test_importing_builds_one_embedding_session(self):
		with counted_sessions() as built:
			memory.import_json(FIXTURE)
		self.assertEqual(len(built), 1)


class TestPrivateDataGuards(FrappeTestCase):
	def test_private_is_git_ignored_and_test_sh_guards_it(self):
		self.assertIn("private/", (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())
		script = (ROOT / "dev" / "test.sh").read_text(encoding="utf-8")
		self.assertIn("git ls-files ../private", script)
		self.assertIn("--exclude-dir=private", script)
