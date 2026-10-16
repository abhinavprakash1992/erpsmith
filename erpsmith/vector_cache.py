"""Per-worker cache of a vector table, invalidated by a version counter in Redis.

A search needs every vector of a table as one matrix. Reading and decoding them takes tens of
milliseconds, so each worker process keeps the decoded matrix and rebuilds it only when the table's
version in Redis has moved. Every write to the table moves it (see `invalidate`).

The token alone is not enough. MariaDB runs REPEATABLE READ: a request whose transaction already read
something has its read view fixed at that first SELECT. If another process then commits a note and moves
the token, this request sees the new token but reads the table through the old view, and would cache rows
without the note under the new token for every later request. So a snapshot also records a fingerprint of
the table (row count and newest `modified`) taken in the same transaction as its rows, and is reused only
while the token and a freshly read fingerprint both match. A snapshot built from a stale view then differs
from the fingerprint of the next, fresh transaction and is rebuilt.
"""

import threading
import time

import frappe
import numpy as np
from frappe.query_builder.functions import Count, Max
from redis.exceptions import RedisError

from erpsmith import vectors

_snapshots = {}  # (site, doctype, fields) -> Snapshot
# ponytail: one entry per (site, doctype, fields) a worker serves, never evicted: fine for a few sites; add an LRU if a worker serves many
_lock = threading.Lock()  # ponytail: one lock for every table; per-table locks if rebuilds ever queue up


class Snapshot:
	__slots__ = ("token", "fingerprint", "rows", "matrix")

	def __init__(self, token, fingerprint, rows, matrix):
		self.token, self.fingerprint, self.rows, self.matrix = token, fingerprint, rows, matrix


def redis_key(doctype):
	"""The site-prefixed key that holds the table's version token."""
	return frappe.cache().make_key(f"erpsmith_vectors_version:{doctype}")


def _start(client, key):
	# nx: never overwrite a live token. A time-based start keeps a restarted (flushed) key above every
	# value a worker may still have cached; a counter that restarted at 1 could repeat an old number.
	client.set(key, time.time_ns(), nx=True)


def version(doctype):
	"""The table's current version token, or None when Redis cannot be reached (callers then skip the cache)."""
	try:
		# raw get/set/incr on a key make_key already prefixed with the site, because get_value/set_value cannot do the
		# atomic set-if-absent and incr the version token needs
		client, key = frappe.cache(), redis_key(doctype)  # nosemgrep
		token = client.get(key)
		if token is None:
			_start(client, key)
			token = client.get(key)
		return token
	except RedisError:
		return None


def bump(doctype):
	"""Move the table's version, so every worker rebuilds its snapshot on the next call."""
	try:
		client, key = frappe.cache(), redis_key(doctype)
		_start(client, key)
		client.incr(key)
	except RedisError:
		frappe.logger().warning("erpsmith: could not bump the version of %s", doctype, exc_info=True)


def invalidate(doctype):
	"""Call after a row of `doctype` changed. Bumps now (readers in this very transaction), after the commit
	(readers that cached the old rows while the commit was in flight) and after a rollback (readers that
	cached rows which no longer exist). The tool wrapper runs the rollback callbacks after a savepoint rollback."""
	bump(doctype)
	frappe.db.after_commit.add(lambda: bump(doctype))
	frappe.db.after_rollback.add(lambda: bump(doctype))


def _fingerprint(doctype):
	"""(row count, newest `modified`) of the table in this transaction's view. `doctype` is always one of
	the app's own internal DocTypes, never user input."""
	table = frappe.qb.DocType(doctype)
	return tuple(frappe.qb.from_(table).select(Count("*"), Max(table.modified)).run()[0])


def _build(doctype, fields, token):
	rows, vecs = [], []
	fingerprint = _fingerprint(doctype)  # same transaction as the rows below
	# get_all skips permission checks on purpose: the tools check read permission before they get here
	for row in frappe.get_all(doctype, fields=["name", "vector", *fields], order_by="creation asc, name asc"):
		try:
			vecs.append(vectors.from_b64(row.pop("vector") or ""))
		except ValueError:
			continue  # a row with no usable vector cannot be ranked
		rows.append(row)
	matrix = np.stack(vecs) if vecs else np.zeros((0, vectors.DIM), dtype=np.float32)
	return Snapshot(token, fingerprint, rows, matrix)


def _current(snap, doctype, token):
	return (snap is not None and token is not None and snap.token == token
		and snap.fingerprint == _fingerprint(doctype))


def snapshot(doctype, fields=()):
	"""This site's decoded vectors for `doctype`; `rows[i]` carries `name` plus `fields`."""
	key = (frappe.local.site, doctype, tuple(fields))
	# token first: a write that moves it after this read makes the next call rebuild. A stale transaction view
	# (see the module docstring) is what the fingerprint catches.
	token = version(doctype)
	snap = _snapshots.get(key)
	if _current(snap, doctype, token):
		return snap
	with _lock:
		snap = _snapshots.get(key)
		if _current(snap, doctype, token):
			return snap
		snap = _build(doctype, tuple(fields), token)
		if token is not None:
			_snapshots[key] = snap
		return snap


def search(doctype, query_vector, k, fields=(), where=None):
	"""[(row, cosine)] for the k rows closest to `query_vector`, best first. `where` is {field: value} over
	the cached `fields`."""
	missing = [f for f in (where or {}) if f not in fields]
	if missing:
		raise ValueError(f"where names {missing}, which are not in the cached fields {list(fields)}")
	snap = snapshot(doctype, fields)
	rows, matrix = snap.rows, snap.matrix
	if where:
		keep = [i for i, r in enumerate(rows) if all(r.get(f) == v for f, v in where.items())]
		rows, matrix = [rows[i] for i in keep], matrix[keep]
	return [(rows[i], score) for i, score in vectors.top_k(matrix, query_vector, k)]
