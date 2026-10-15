"""Memory: notes about this site (sandbox facts, code patterns, bug fixes, context) that are found by meaning.

A note is one MCP Memory row, shared by every user of the site. The DocType controller embeds
"title + blank line + content" whenever either changes, so search stays right however a note is edited.
"""

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import frappe
from frappe.utils import get_datetime, now_datetime

from erpsmith import vector_cache, vectors
from erpsmith.registry import InvalidArguments, tool

DOCTYPE = "MCP Memory"
TOPICS = ("sandbox-rule", "pattern", "bug-fix", "context", "handoff")
MAX_TITLE_CHARS = 140  # the Data column
MAX_CONTENT_CHARS = 20_000  # the longest note in a real 89-note memory is 2,531; embed() reads 512 tokens anyway
MAX_QUERY_CHARS = 4000  # a search query is cut here, not rejected (512 tokens are about 2,000 characters)


def note_text(title, content):
	"""What gets embedded for a note."""
	return f"{title or ''}\n\n{content or ''}"


def make_id(topic, title):
	"""A readable id, `<topic>__<slug>__<yyyymmddhhmmss>` like the Node server's, made unique with -2, -3 ..."""
	slug = re.sub(r"[^a-z0-9]+", "-", (title or "untitled").lower()).strip("-")[:50]
	base = f"{topic}__{slug}__{now_datetime().strftime('%Y%m%d%H%M%S')}"
	name, n = base, 1
	while frappe.db.exists(DOCTYPE, name):
		n += 1
		name = f"{base}-{n}"
	return name


def clamp(name, value, default, low, high):
	"""An int from a tool argument: `default` for None, otherwise clamped to low..high (floats are truncated)."""
	if value is None:
		return default
	try:
		number = float(value)
	except OverflowError:  # an int too big for a float, such as 10**400
		return high if value > 0 else low
	except (TypeError, ValueError):
		raise InvalidArguments(f"{name} must be a number") from None
	if isinstance(value, bool) or number != number:  # True is not a count; NaN is not a number
		raise InvalidArguments(f"{name} must be a number")
	return int(max(low, min(high, number)))


def check_topic(topic):
	if topic not in TOPICS:
		raise InvalidArguments(f'Invalid topic "{topic}". Use one of: {", ".join(TOPICS)}')


def required_text(name, value):
	if not isinstance(value, str) or not value.strip():
		raise InvalidArguments(f"{name} is required (a non-empty string)")
	return value


def _optional_text(name, value):
	return None if value is None else required_text(name, value)


def _iso(moment):
	return get_datetime(moment).isoformat(timespec="seconds")


def _must_exist(id):
	if not frappe.db.exists(DOCTYPE, id):
		raise frappe.DoesNotExistError(f'No memory with id "{id}"')


@tool("search_memory")
def search_memory(query, topK=5, topic=None):
	frappe.has_permission(DOCTYPE, "read", throw=True)
	query = required_text("query", query)[:MAX_QUERY_CHARS]
	if topic:
		check_topic(topic)
	k = clamp("topK", topK, 5, 1, 20)
	hits = vector_cache.search(DOCTYPE, vectors.embed(query), k, ("topic",), {"topic": topic} if topic else None)
	notes = {r.name: r for r in frappe.get_all(
		DOCTYPE, filters={"name": ["in", [row.name for row, _ in hits]]}, fields=["name", "topic", "title", "content"])}
	results = [
		{"id": row.name, "topic": notes[row.name].topic, "title": notes[row.name].title,
			"content": notes[row.name].content, "score": round(score, 4)}
		for row, score in hits if row.name in notes  # a note deleted since the snapshot is skipped
	]
	return {"ok": True, "query": query, "count": len(results), "results": results}


@tool("add_memory")
def add_memory(topic, title, content):
	check_topic(topic)
	doc = frappe.get_doc({"doctype": DOCTYPE, "topic": topic, "title": required_text("title", title),
		"content": required_text("content", content)}).insert()
	return {"ok": True, "action": "added", "id": doc.name, "topic": topic, "title": doc.title}


@tool("update_memory")
def update_memory(id, content=None, title=None):  # noqa: A002  the argument name is part of the tool's schema
	id = required_text("id", id)
	title, content = _optional_text("title", title), _optional_text("content", content)
	if title is None and content is None:
		raise InvalidArguments("pass a new title, new content, or both")
	frappe.has_permission(DOCTYPE, "write", throw=True)
	_must_exist(id)
	doc = frappe.get_doc(DOCTYPE, id)
	if title is not None:
		doc.title = title
	if content is not None:
		doc.content = content
	doc.save()
	return {"ok": True, "action": "updated", "id": id}


@tool("delete_memory")
def delete_memory(id):  # noqa: A002
	id = required_text("id", id)
	frappe.has_permission(DOCTYPE, "delete", throw=True)
	_must_exist(id)
	frappe.delete_doc(DOCTYPE, id)
	return {"ok": True, "action": "deleted", "id": id}


@tool("list_memory")
def list_memory(topic=None, limit=20):
	frappe.has_permission(DOCTYPE, "read", throw=True)
	if topic:
		check_topic(topic)
	n = clamp("limit", limit, 20, 1, 100)
	rows = frappe.get_all(DOCTYPE, filters={"topic": topic} if topic else {},
		fields=["name", "topic", "title", "creation"], order_by="creation desc, name desc")
	entries = [{"id": r.name, "topic": r.topic, "title": r.title, "created_at": _iso(r.creation)} for r in rows[:n]]
	return {"ok": True, "count": len(entries), "total": len(rows), "entries": entries}


@tool("dump_memory")
def dump_memory(path=None):  # `path` is accepted for old callers and ignored: nothing is written on the server
	frappe.has_permission(DOCTYPE, "read", throw=True)
	# ponytail: no row or size cap (the Node server capped it at 10,000 rows); add one if a site's memory grows large
	rows = frappe.get_all(DOCTYPE, fields=["name", "topic", "title", "content", "creation"], order_by="creation asc, name asc")
	parts = [f"# Memory Export\n\nGenerated from the memory table. {len(rows)} entries. The table is canonical; "
		"edits to this text are not read back unless re-imported.\n"]
	for topic in TOPICS:
		notes = [r for r in rows if r.topic == topic]
		if notes:
			parts.append(f"\n---\n\n## {topic}\n")
			parts += [f"\n### {r.title}\n\n_id: `{r.name}` · {_iso(r.creation)}_\n\n{r.content}\n" for r in notes]
	return {"ok": True, "action": "dumped", "entries": len(rows), "markdown": "".join(parts)}


SEED_PATH = Path(__file__).with_name("seed_memory.json")
APPLIED_DEFAULT = "erpsmith_seeds_applied"
SEED_KEY = re.compile(r"[a-z0-9][a-z0-9-]{0,60}")  # used with fullmatch: "$" would accept a trailing newline


def check_seeds(seeds):
	"""ValueError unless `seeds` is a list of {seed_key, topic, title, content} with unique keys and valid text."""
	if not isinstance(seeds, list):
		raise ValueError("a seed file is a JSON list of notes")
	keys = set()
	for i, seed in enumerate(seeds):
		if not isinstance(seed, dict) or set(seed) != {"seed_key", "topic", "title", "content"}:
			raise ValueError(f"seed {i}: expected exactly seed_key, topic, title and content")
		key = seed["seed_key"]
		if not isinstance(key, str) or not SEED_KEY.fullmatch(key):
			raise ValueError(f"seed {i}: bad seed_key {key!r} (lowercase letters, digits and dashes)")
		if key in keys:
			raise ValueError(f"seed {i}: duplicate seed_key {key!r}")
		keys.add(key)
		if seed["topic"] not in TOPICS:
			raise ValueError(f"seed {key}: topic must be one of {', '.join(TOPICS)}")
		if not all(isinstance(seed[f], str) and seed[f].strip() for f in ("title", "content")):
			raise ValueError(f"seed {key}: title and content must be non-empty strings")
		if len(seed["title"]) > MAX_TITLE_CHARS or len(seed["content"]) > MAX_CONTENT_CHARS:
			raise ValueError(f"seed {key}: title or content is too long")


def _applied_seed_keys():
	# Read the table, not frappe.defaults: its cache can outlive a rolled-back transaction.
	raw = frappe.db.get_value("DefaultValue", {"parent": "__default", "defkey": APPLIED_DEFAULT}, "defvalue")
	return set(json.loads(raw)) if raw else set()


def load_seeds(path=None):
	"""Insert the seed notes this site has never received. Safe to run on every install and migrate:
	a key already applied is skipped (so a note the user deleted stays deleted), a key whose note exists
	but was never recorded is adopted, and a run with nothing new embeds nothing."""
	seeds = json.loads(Path(path or SEED_PATH).read_text(encoding="utf-8"))
	check_seeds(seeds)
	applied = _applied_seed_keys()
	todo = [s for s in seeds if s["seed_key"] not in applied]
	inserted = present = 0
	for seed in todo:
		if frappe.db.exists(DOCTYPE, {"seed_key": seed["seed_key"]}):
			present += 1  # an earlier run died before it recorded the key
		else:
			frappe.get_doc({"doctype": DOCTYPE, "name": f"seed__{seed['seed_key']}", "seed_key": seed["seed_key"],
				"topic": seed["topic"], "title": seed["title"], "content": seed["content"]}).insert(ignore_permissions=True)
			inserted += 1
		applied.add(seed["seed_key"])
	if todo:
		frappe.db.set_default(APPLIED_DEFAULT, json.dumps(sorted(applied)))
	return {"inserted": inserted, "already_present": present}


def _utc_to_site(stamp):
	"""'2026-06-22T06:40:43.101Z' (UTC, as the Node server wrote it) as the site's local naive datetime, the way Frappe stores `creation`."""
	if not stamp:
		return None
	moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
	if moment.tzinfo is None:
		raise ValueError(f"created_at {stamp!r} has no time zone")
	return moment.astimezone(ZoneInfo(frappe.utils.get_system_timezone())).replace(tzinfo=None)


def import_json(path):
	"""Import notes exported from the Node server by dev/export-node-memory.mjs, keeping their ids and dates.

	    bench --site <site> execute erpsmith.memory.import_json --kwargs "{'path': '/workspace/erpsmith/private/node-memory.json'}"

	Everything is embedded in this one process, so the model loads once. Does not commit: bench execute does."""
	entries = json.loads(Path(path).read_text(encoding="utf-8"))
	if not isinstance(entries, list):
		raise ValueError("an export is a JSON list of notes")
	imported, present, rejected = 0, 0, []
	for entry in entries:
		note_id = entry.get("id") if isinstance(entry, dict) else None
		frappe.db.savepoint("import_note")
		try:
			if not isinstance(entry, dict):
				raise TypeError("expected an object")
			if not isinstance(note_id, str) or not note_id:
				raise ValueError("every note needs an id")
			created = _utc_to_site(entry.get("created_at"))
			if frappe.db.exists(DOCTYPE, note_id):
				present += 1
				continue
			frappe.get_doc({"doctype": DOCTYPE, "name": note_id, "topic": entry["topic"], "title": entry["title"],
				"content": entry["content"]}).insert(ignore_permissions=True)
			if created:
				frappe.db.set_value(DOCTYPE, note_id, "creation", created, update_modified=False)
			imported += 1
		except Exception as e:  # one bad note must not stop the rest
			frappe.db.rollback(save_point="import_note")
			rejected.append({"id": note_id, "reason": f"{type(e).__name__}: {e}"})
	return {"imported": imported, "already_present": present, "rejected": rejected}
