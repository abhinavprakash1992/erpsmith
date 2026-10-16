"""Record CRUD: in-process equivalents of the REST /api/resource endpoints, with the same permission checks."""

import frappe
import frappe.client

from erpsmith.registry import tool

MAX_LIMIT = 200


# `flags` in get_doc would let a caller set ignore_permissions; `owner` and `creation` would forge who made a
# record and when, which "if owner" permission rules and audit trails rely on. Frappe sets the real values itself.
DROPPED_KEYS = {"flags", "owner", "creation"}


def _clean(meta, data):
	"""Drop DROPPED_KEYS from the record and from its child rows. Other values (a JSON field's content) are left alone."""
	data = {k: v for k, v in data.items() if k not in DROPPED_KEYS}
	for df in meta.get_table_fields():
		if isinstance(data.get(df.fieldname), list):
			data[df.fieldname] = [
				{k: v for k, v in row.items() if k not in DROPPED_KEYS} if isinstance(row, dict) else row
				for row in data[df.fieldname]
			]
	return data


def _new_rows(meta, data, own_rows=lambda fieldname: ()):
	"""Rows in `data` that carry a `name` are saved as an UPDATE of that row, whichever document owns it,
	and move under this one. Keep a name only on rows the document already owns; the rest are inserted."""
	data = dict(data)
	for df in meta.get_table_fields():
		rows = data.get(df.fieldname)
		if isinstance(rows, list):
			own = own_rows(df.fieldname)
			data[df.fieldname] = [
				{k: v for k, v in row.items() if k != "name"} if isinstance(row, dict) and row.get("name") not in own else row
				for row in rows
			]
	return data


@tool("create_record")
def create_record(doctype, data):
	meta = frappe.get_meta(doctype)
	data = _new_rows(meta, _clean(meta, data))
	if frappe.is_table(doctype):
		data.pop("name", None)  # a child row with a name would take over that row of another document
	doc = frappe.client.insert({**data, "doctype": doctype})
	return {"name": doc["name"], "doctype": doctype, "data": doc}


@tool("get_record")
def get_record(doctype, name):
	return {"doctype": doctype, "name": name, "data": frappe.client.get(doctype, name)}


@tool("update_record")
def update_record(doctype, name, data):
	doc = frappe.get_doc(doctype, name, for_update=True)
	# Check the stored record before applying `data`. save() checks again, but against a document that
	# `data` has already edited, so it could forge what permission hooks read (owner, allocated_to).
	doc.check_permission("write")
	# doctype and name pick the record, they are not fields: letting them through retargets the save.
	data = {k: v for k, v in _clean(doc.meta, data).items() if k not in ("doctype", "name")}
	doc.update(_new_rows(doc.meta, data, lambda fieldname: {row.name for row in doc.get(fieldname)}))
	doc.save()
	if doc.get("parenttype"):
		# As the REST API does: a changed child row is saved through its parent, so the parent validates and recomputes.
		frappe.get_doc(doc.parenttype, doc.parent).save()
	doc.apply_fieldlevel_read_permissions()
	return {"updated": name, "doctype": doctype, "data": doc.as_dict()}


@tool("query_records")
def query_records(doctype, filters=None, fields=None, limit=50, orderBy=None):
	data = frappe.client.get_list(
		doctype,
		fields=fields or ["name"],
		filters=filters or [],
		order_by=orderBy or "modified desc",
		# Frappe reads a limit of 0 as "no limit", so 0 and None mean the default and negatives mean 1.
		limit_page_length=max(1, min(int(limit or 50), MAX_LIMIT)),
	)
	return {"doctype": doctype, "count": len(data), "data": data}


@tool("delete_record")
def delete_record(doctype, name):
	frappe.client.delete(doctype, name)
	return {"deleted": True, "doctype": doctype, "name": name}
