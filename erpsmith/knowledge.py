"""Knowledge: this site's DocType structure and the ERPNext user manual, searchable by meaning.

Chunks live in MCP Knowledge Chunk. `build` (one background job, Task 10) keeps them current: DocType chunks
come from frappe.get_meta, so they match this site's version and customizations; manual chunks come from
docs.erpnext.com. A chunk is embedded again only when its content hash changed.
"""

import hashlib
from collections import defaultdict
from urllib.parse import quote

import frappe

from erpsmith import vector_cache, vectors

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
		infos.append((meta, [f for f in meta.fields if f.fieldtype not in LAYOUT], apps.get(meta.module, "")))
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
