"""Knowledge: this site's DocType structure and the ERPNext user manual, searchable by meaning.

Chunks live in MCP Knowledge Chunk. `build` (one background job, Task 10) keeps them current: DocType chunks
come from frappe.get_meta, so they match this site's version and customizations; manual chunks come from
docs.erpnext.com. A chunk is embedded again only when its content hash changed.
"""

import hashlib
import json
import re
from collections import defaultdict
from urllib.parse import quote

import frappe
import requests

from erpsmith import memory, vector_cache, vectors
from erpsmith.registry import InvalidArguments, tool

DOCTYPE = "MCP Knowledge Chunk"
OWN_MODULE = "ERPSmith"
LAYOUT = frozenset({"Section Break", "Column Break", "Tab Break", "HTML", "Fold", "Heading", "Button", "Image"})
LINKISH = frozenset({"Link", "Table", "Table MultiSelect"})
SUMMARY_LABELS = 15  # field labels in the embedded summary
REFS_SHOWN = 60  # "Referenced by" entries in a DocType chunk


def _hash(chunk):
	parts = [chunk["kind"], chunk["title"], chunk["source"], chunk["summary"], chunk["content"]]
	return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _field_line(f, custom):
	kind = f.fieldtype
	if f.options and (f.fieldtype in LINKISH or f.fieldtype == "Dynamic Link"):
		kind += f"→{f.options}"
	elif f.fieldtype == "Select" and f.options:
		kind += ": " + " | ".join(o for o in f.options.split("\n") if o)[:160]
	label = f' "{f.label}"' if f.label else ""
	return f"- {f.fieldname} ({kind}){label}{' *required' if f.reqd else ''}{' [custom]' if f.fieldname in custom else ''}"


def _doctype_chunk(meta, fields, app, refs, parents, custom, intros):
	"""The chunk for one DocType. Port of the Node build: the content is the full record a search returns;
	the summary is plain English (labels, parents, the manual's intro), because the embedder reads about
	256 tokens and matches prose far better than a list of field names."""
	name = meta.name
	traits = [text for flag, text in (
		(meta.get("istable"), "child table"),
		(meta.get("issingle"), "single (settings)"),
		(meta.get("is_submittable"), "submittable (Draft → Submitted → Cancelled)"),
		(meta.get("is_tree"), "tree"),
		(meta.get("custom"), "custom DocType"),
	) if flag]
	description = meta.get("description")
	links = [f"{f.options} ({f.fieldname})" for f in fields if f.fieldtype == "Link" and f.options]
	tables = [f for f in fields if f.fieldtype.startswith("Table") and f.options]
	by = refs.get(name, [])
	content = "\n".join(filter(None, [
		f"DocType: {name}",
		f"App: {app} | Module: {meta.module} | DB table: `tab{name}`" + (f" | {', '.join(traits)}" if traits else ""),
		description and f"Description: {description}",
		meta.get("autoname") and f"Naming: {meta.autoname}",
		tables and "Child tables: " + ", ".join(f"{f.fieldname} → {f.options}" for f in tables),
		links and "Links to: " + ", ".join(links),
		by and "Referenced by: " + ", ".join(by[:REFS_SHOWN]) + (f", … +{len(by) - REFS_SHOWN} more" if len(by) > REFS_SHOWN else ""),
		"\nFields:\n" + "\n".join(_field_line(f, custom.get(name, ())) for f in fields),
	]))
	labels = list(dict.fromkeys(f.label for f in fields if f.label))[:SUMMARY_LABELS]
	summary = " ".join(filter(None, [
		f"{name}{': ' + description if description else ''}.",
		intros.get(name),
		f"{meta.module} module, {app}." + (f" It is a {', '.join(traits)}." if traits else ""),
		meta.get("istable") and parents.get(name) and f"Line items / rows of {', '.join(dict.fromkeys(parents[name]))}.",
		tables and "Has child tables: " + ", ".join(f.options for f in tables) + ".",
		labels and "Fields: " + ", ".join(labels) + ".",
	]))
	return {"kind": "doctype", "chunk_key": f"doctype::{name}", "title": name, "source": f"/app/doctype/{quote(name)}",
		"summary": summary, "content": content}


def doctype_chunks(intros=None, only=None):
	"""One chunk per DocType on this site. They come from get_meta, so custom fields, property setters and
	custom apps are included. `only` limits the set to the named DocTypes."""
	intros = intros or {}
	apps = {m.name: m.app_name for m in frappe.get_all("Module Def", fields=["name", "app_name"])}
	custom = defaultdict(set)
	for row in frappe.get_all("Custom Field", fields=["dt", "fieldname"]):
		custom[row.dt].add(row.fieldname)
	infos = []
	for row in frappe.get_all("DocType", fields=["name", "module"], order_by="name asc"):
		if row.module == OWN_MODULE or (only is not None and row.name not in only):
			continue
		try:
			meta = frappe.get_meta(row.name)
		except Exception:
			frappe.log_error(title=f"ERPSmith: cannot read the DocType {row.name}")
			continue
		infos.append((meta, [f for f in meta.fields if f.fieldtype not in LAYOUT], apps.get(meta.module) or ""))
	refs, parents = defaultdict(list), defaultdict(list)  # target DocType -> "Source.field", child -> parents
	for meta, fields, _ in infos:
		for f in fields:
			if f.fieldtype in LINKISH and f.options:
				refs[f.options].append(f"{meta.name}.{f.fieldname}" + ("" if f.fieldtype == "Link" else f" ({f.fieldtype})"))
				if f.fieldtype != "Link":
					parents[f.options].append(meta.name)
	return [_doctype_chunk(meta, fields, app, refs, parents, custom, intros) for meta, fields, app in infos]


def sync(chunks, delete_kinds, commit_every=0):
	"""Make the table hold `chunks`: insert new ones, embed again those whose hash changed, delete stored
	chunks that are missing from `chunks` and whose kind is in `delete_kinds`. Only changed chunks are
	embedded. With `commit_every`, commit (and move the cache version) after that many writes."""
	existing = {r.name: r.content_hash for r in frappe.get_all(DOCTYPE, fields=["name", "content_hash"])}
	counts = {"added": 0, "updated": 0, "unchanged": 0, "deleted": 0}
	seen = set()
	for chunk in chunks:
		key = chunk["chunk_key"]
		seen.add(key)
		digest = _hash(chunk)
		if existing.get(key) == digest:
			counts["unchanged"] += 1
			continue
		values = {"kind": chunk["kind"], "title": chunk["title"], "source": chunk["source"], "summary": chunk["summary"],
			"content": chunk["content"], "content_hash": digest,
			"vector": vectors.to_b64(vectors.embed(chunk["summary"] or chunk["content"] or ""))}
		if key in existing:
			frappe.db.set_value(DOCTYPE, key, values)  # moves `modified`, which the cache fingerprint reads
			counts["updated"] += 1
		else:
			doc = frappe.get_doc({"doctype": DOCTYPE, "chunk_key": key, **values})
			doc.flags.skip_invalidate = True  # the version moves once per batch below, not once per row
			doc.insert(ignore_permissions=True)
			counts["added"] += 1
		if commit_every and (counts["added"] + counts["updated"]) % commit_every == 0:
			frappe.db.commit()
			vector_cache.bump(DOCTYPE)
	stale = [k for k in existing if k not in seen and k.split("::", 1)[0] in delete_kinds]
	for i in range(0, len(stale), 200):
		frappe.db.delete(DOCTYPE, {"name": ["in", stale[i:i + 200]]})
	counts["deleted"] = len(stale)
	vector_cache.invalidate(DOCTYPE)
	if commit_every:
		frappe.db.commit()
	return counts


MANUAL_URL = "https://docs.erpnext.com/api/resource/Wiki%20Page"
MANUAL_BASE = "https://docs.erpnext.com/"
CHUNK_CHARS = 1500
MIN_CHUNK_CHARS = 80
COMMIT_EVERY = 200


def fetch_manual():
	"""The published user manual's pages [{title, route, content}], or None when they cannot be read
	(logged; the build goes on without the manual)."""
	try:
		response = requests.get(MANUAL_URL, timeout=120, params={
			"fields": json.dumps(["title", "route", "content"]),
			"filters": json.dumps([["route", "like", "docs/user/%"], ["published", "=", 1]]),
			"limit_page_length": "0",
		})
		response.raise_for_status()
		pages = response.json()["data"]
		if not isinstance(pages, list):
			raise ValueError("the manual's response has no list of pages")
		pages = [p for p in pages if isinstance(p, dict) and isinstance(p.get("route"), str)]
		if not pages:  # an empty answer must not look like an empty manual: the build would delete every stored chunk
			raise ValueError("the manual's response has no pages")
		return pages
	except Exception:
		frappe.log_error(title="ERPSmith: could not fetch the ERPNext manual")
		return None


def _clean(markdown):
	markdown = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", markdown)  # images
	markdown = re.sub(r"<[^>]+>", "", markdown)  # stray HTML
	return re.sub(r"\n{3,}", "\n\n", markdown).strip()


def _split_long(text):
	if len(text) <= CHUNK_CHARS:
		return [text]
	parts, current = [], ""
	for paragraph in text.split("\n\n"):
		if current and len(current) + len(paragraph) > CHUNK_CHARS:
			parts.append(current)
			current = ""
		current += ("\n\n" if current else "") + paragraph
	if current:
		parts.append(current)
	return parts


def manual_chunks(pages):
	"""Chunks of the manual: a page is cut at its '## ' headings, long sections at paragraph breaks, and
	every chunk starts with the page title so it makes sense on its own."""
	chunks, routes = [], set()
	for page in pages:
		if not isinstance(page, dict) or not isinstance(page.get("route"), str) or page["route"] in routes:
			continue  # a page seen twice under one route would repeat its chunk keys: the first one wins
		routes.add(page["route"])
		title, content = page.get("title"), page.get("content")
		title = title.strip() if isinstance(title, str) else ""
		title = title or page["route"].rstrip("/").rsplit("/", 1)[-1] or "untitled"  # the title column is mandatory
		digest = hashlib.sha256(page["route"].encode("utf-8")).hexdigest()[:16]
		n = 0
		for section in re.split(r"\n(?=## )", _clean(content if isinstance(content, str) else "")):
			for part in _split_long(section):
				if len(part) < MIN_CHUNK_CHARS:
					continue
				text = f"# {title}\n\n{part}"
				chunks.append({"kind": "manual", "chunk_key": f"manual::{digest}::{n}", "title": title[:140],
					"source": MANUAL_BASE + page["route"], "summary": text, "content": text})
				n += 1
	return chunks


def _intro(content):
	"""The text of a manual chunk after its '# title' line, cut to 500 characters."""
	return content.split("\n\n", 1)[1][:500] if "\n\n" in content else ""


def _intros(chunks):
	"""{page title: opening text of its first chunk}, used as the 'what is this for' prose in a DocType's summary.
	Pages that share a title: the lowest chunk_key wins, the same rule as _stored_intros."""
	intros = {}
	for chunk in sorted(chunks, key=lambda c: c["chunk_key"]):
		if chunk["chunk_key"].endswith("::0"):
			intros.setdefault(chunk["title"], _intro(chunk["content"]))
	return intros


def _stored_intros():
	"""The same, read from the manual chunks already stored, for a build that could not fetch the manual."""
	rows = frappe.get_all(DOCTYPE, filters={"kind": "manual", "chunk_key": ["like", "%::0"]},
		fields=["title", "content"], order_by="chunk_key asc")
	intros = {}
	for r in rows:
		intros.setdefault(r.title, _intro(r.content))
	return intros


def build(include_manual=True, commit=True, only_doctypes=None):
	"""Bring the knowledge chunks up to date. Runs as ONE background job (an RQ work horse is forked per job,
	so the embedding model loads once per build, not once per chunk) and never calls mcp.call_tool.

	    bench --site <site> execute erpsmith.knowledge.build

	A manual that cannot be fetched is logged and skipped: the stored manual chunks stay, and the DocType chunks keep
	the intros they had. Stale DocType chunks are deleted only on a full build (`only_doctypes` unset), stale manual
	chunks only when the manual was fetched."""
	pages = fetch_manual() if include_manual else None
	manual = manual_chunks(pages) if pages is not None else []
	intros = _intros(manual) if pages is not None else _stored_intros()
	chunks = doctype_chunks(intros=intros, only=only_doctypes) + manual
	delete_kinds = (("doctype",) if only_doctypes is None else ()) + (("manual",) if pages is not None else ())
	counts = sync(chunks, delete_kinds, COMMIT_EVERY if commit else 0)
	counts["manual"] = (f"{len(pages)} pages" if pages is not None
		else "not requested" if not include_manual else "skipped: could not be fetched")
	return counts


def enqueue_build():
	"""Queue the build as one long job (once: a second request while it waits or runs is dropped). A failure to
	queue it, for example Redis being down during an install, is logged and must not fail the install."""
	try:
		frappe.enqueue("erpsmith.knowledge.build", queue="long", timeout=3600, job_id="erpsmith-knowledge-build",
			deduplicate=True, enqueue_after_commit=True)
	except Exception:
		frappe.log_error(title="ERPSmith: could not queue the knowledge build")


MAX_RESULT_CHARS = 6000  # a big DocType (Sales Invoice) is about 15,000 characters; keep results cheap
KINDS = {"doctype": "doctype", "manual": "manual", "doc": "manual"}  # "doc" is the Node server's word for the manual
EMPTY_NOTE = ("The knowledge index is empty. It is built in the background after install or migrate; "
	"to build it now, run: bench --site <site> execute erpsmith.knowledge.build")


@tool("search_erpnext_knowledge")
def search_erpnext_knowledge(query, topK=5, kind=None):
	frappe.has_permission(DOCTYPE, "read", throw=True)
	query = memory.required_text("query", query)[:memory.MAX_QUERY_CHARS]
	if kind and kind not in KINDS:
		raise InvalidArguments('kind must be "doctype" or "manual" ("doc" also means "manual")')
	k = memory.clamp("topK", topK, 5, 1, 20)
	hits = vector_cache.search(DOCTYPE, vectors.embed(query), k, ("kind",), {"kind": KINDS[kind]} if kind else None)
	chunks = {r.name: r for r in frappe.get_all(
		DOCTYPE, filters={"name": ["in", [row.name for row, _ in hits]]}, fields=["name", "kind", "title", "source", "content"])}
	results = []
	for row, score in hits:
		chunk = chunks.get(row.name)
		if chunk is None:  # deleted since the snapshot
			continue
		content = chunk.content or ""
		if len(content) > MAX_RESULT_CHARS:
			content = content[:MAX_RESULT_CHARS] + f"\n… [truncated at {MAX_RESULT_CHARS} characters]"
		results.append({"kind": chunk.kind, "title": chunk.title, "source": chunk.source, "content": content,
			"score": round(score, 4)})
	out = {"ok": True, "query": query, "count": len(results), "results": results}
	if not results and not frappe.db.count(DOCTYPE):
		out["note"] = EMPTY_NOTE
	return out
