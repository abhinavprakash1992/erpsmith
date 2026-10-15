import subprocess
import sys
from unittest.mock import patch

import frappe
import numpy as np
from frappe.tests.utils import FrappeTestCase
from redis.exceptions import ConnectionError as RedisConnectionError

from erpsmith import vector_cache, vectors
from erpsmith.tests.helpers import empty_memory_table

DOCTYPE = "MCP Memory"


def note(title, content="body", topic="pattern"):
	return frappe.get_doc({"doctype": DOCTYPE, "topic": topic, "title": title, "content": content})


class TestVersionToken(FrappeTestCase):
	def test_bump_moves_one_tables_version_only(self):
		a, b = vector_cache.version("Table A"), vector_cache.version("Table B")
		vector_cache.bump("Table A")
		self.assertNotEqual(vector_cache.version("Table A"), a)
		self.assertEqual(vector_cache.version("Table B"), b)

	def test_a_flushed_key_restarts_above_every_old_value(self):
		old = vector_cache.version(DOCTYPE)
		frappe.cache().delete(vector_cache.redis_key(DOCTYPE))  # what bench clear-cache does to the key
		new = vector_cache.version(DOCTYPE)
		self.assertNotEqual(new, old)
		self.assertGreater(int(new), int(old))

	def test_unreachable_redis_means_no_version(self):
		with patch.object(frappe.cache(), "get", side_effect=RedisConnectionError("down")):
			self.assertIsNone(vector_cache.version(DOCTYPE))


class TestSnapshots(FrappeTestCase):
	def setUp(self):
		vector_cache._snapshots.clear()
		empty_memory_table()

	def tearDown(self):
		vector_cache._snapshots.clear()

	def top(self, text, k=1, where=None):
		hits = vector_cache.search(DOCTYPE, vectors.embed(text), k, ("topic",), where)
		return [row.name for row, _ in hits]

	def test_search_ranks_by_meaning(self):
		a = note("Importing json", "RULE 1: no import statements in server scripts").insert()
		note("Warehouses", "Warehouse naming convention for stores").insert()
		self.assertEqual(self.top("can I import json in a server script"), [a.name])

	def test_the_snapshot_is_reused_until_the_version_moves(self):
		note("one").insert()
		with patch.object(vector_cache, "_build", wraps=vector_cache._build) as build:
			vector_cache.snapshot(DOCTYPE, ("topic",))
			vector_cache.snapshot(DOCTYPE, ("topic",))
			self.assertEqual(build.call_count, 1)
			vector_cache.bump(DOCTYPE)
			vector_cache.snapshot(DOCTYPE, ("topic",))
			self.assertEqual(build.call_count, 2)

	def test_writes_are_visible_to_the_next_search(self):
		a = note("Importing json", "RULE 1: no import statements in server scripts").insert()
		b = note("Warehouses", "Warehouse naming convention for stores").insert()
		query = "can I import json in a server script"
		self.assertEqual(self.top(query), [a.name])
		frappe.delete_doc(DOCTYPE, a.name)
		self.assertEqual(self.top(query), [b.name])
		c = note("Import statements", "Every import throws ImportError in server scripts").insert()
		self.assertEqual(self.top(query), [c.name])
		c.content = "Warehouse naming convention for stores, stated twice"
		c.title = "Warehouse stores"
		c.save()  # an edit moves the version too: the cached row now holds the new text's vector
		snap = vector_cache.snapshot(DOCTYPE, ("title",))
		row = [r.name for r in snap.rows].index(c.name)
		np.testing.assert_allclose(snap.matrix[row],
			vectors.embed("Warehouse stores\n\nWarehouse naming convention for stores, stated twice"), atol=1e-6)

	def test_a_rolled_back_write_is_not_served(self):
		frappe.db.savepoint("probe")
		note("ghost").insert()
		self.assertIn("ghost", [r.title for r in vector_cache.snapshot(DOCTYPE, ("title",)).rows])
		frappe.db.rollback(save_point="probe")
		frappe.db.after_rollback.run()  # what mcp._undo_tool_writes does after a savepoint rollback
		self.assertNotIn("ghost", [r.title for r in vector_cache.snapshot(DOCTYPE, ("title",)).rows])

	def test_saving_and_deleting_a_note_move_the_version(self):
		v0 = vector_cache.version(DOCTYPE)
		doc = note("moving").insert()
		v1 = vector_cache.version(DOCTYPE)
		doc.content = "changed"
		doc.save()
		v2 = vector_cache.version(DOCTYPE)
		frappe.delete_doc(DOCTYPE, doc.name)
		v3 = vector_cache.version(DOCTYPE)
		self.assertEqual(len({v0, v1, v2, v3}), 4)

	def test_a_bulk_writer_can_skip_the_per_row_invalidation(self):
		v0 = vector_cache.version(DOCTYPE)
		doc = note("quiet")
		doc.flags.skip_invalidate = True
		doc.insert()
		self.assertEqual(vector_cache.version(DOCTYPE), v0)

	def test_rows_without_a_usable_vector_are_skipped(self):
		good, bad = note("Good", "fine").insert(), note("Bad", "broken").insert()
		frappe.db.set_value(DOCTYPE, bad.name, "vector", "not base64 at all", update_modified=False)
		vector_cache.bump(DOCTYPE)
		self.assertEqual([r.name for r in vector_cache.snapshot(DOCTYPE).rows], [good.name])

	def test_where_keeps_only_matching_rows(self):
		note("Pattern note", "send http request", "pattern").insert()
		b = note("Bug note", "send http request", "bug-fix").insert()
		self.assertEqual(self.top("send http request", 5, {"topic": "bug-fix"}), [b.name])
		self.assertEqual(self.top("send http request", 5, {"topic": "handoff"}), [])

	def test_an_empty_table_gives_an_empty_matrix_and_no_hits(self):
		snap = vector_cache.snapshot(DOCTYPE, ("topic",))
		self.assertEqual(snap.matrix.shape, (0, vectors.DIM))
		self.assertEqual(self.top("anything"), [])

	def test_without_a_version_nothing_is_cached(self):
		with patch.object(vector_cache, "version", return_value=None), \
				patch.object(vector_cache, "_build", wraps=vector_cache._build) as build:
			vector_cache.snapshot(DOCTYPE)
			vector_cache.snapshot(DOCTYPE)
		self.assertEqual(build.call_count, 2)
		self.assertEqual(vector_cache._snapshots, {})


class TestAcrossProcesses(FrappeTestCase):
	"""Does not empty the table: this class commits, and a commit would make that delete permanent."""

	def setUp(self):
		vector_cache._snapshots.clear()

	def test_a_write_committed_by_another_process_is_seen(self):
		"""Workers are separate processes: the counter in Redis is what tells this one a row changed."""
		title = f"cross-process probe {frappe.generate_hash(length=8)}"
		self.addCleanup(self._remove_committed_probe, title)
		frappe.db.commit()  # leave this test's database snapshot so rows committed elsewhere become visible
		self.assertNotIn(title, [r.title for r in vector_cache.snapshot(DOCTYPE, ("title",)).rows])
		code = (
			"import frappe\n"
			f"frappe.init(site={frappe.local.site!r}, sites_path='.')\n"
			"frappe.connect()\n"
			"frappe.set_user('Administrator')\n"
			"frappe.get_doc({'doctype': 'MCP Memory', 'topic': 'pattern', "
			f"'title': {title!r}, 'content': 'written by another process'}}).insert()\n"
			"frappe.db.commit()\n"
			"frappe.destroy()\n"
		)
		sites = frappe.utils.get_bench_path() + "/sites"
		subprocess.run([sys.executable, "-c", code], cwd=sites, check=True, capture_output=True, timeout=180)
		frappe.db.commit()  # a new transaction, so this process sees the other one's commit
		self.assertIn(title, [r.title for r in vector_cache.snapshot(DOCTYPE, ("title",)).rows])

	@staticmethod
	def _remove_committed_probe(title):
		frappe.db.rollback()
		frappe.db.delete(DOCTYPE, {"title": title})
		frappe.db.commit()
		vector_cache.bump(DOCTYPE)
