"""Record CRUD: in-process equivalents of the REST /api/resource endpoints, with the same permission checks."""

import frappe
import frappe.client

from erpsmith.mcp import tool

MAX_LIMIT = 200


def _clean(value):
	"""Drop every `flags` key: in get_doc it would let a caller set ignore_permissions."""
	if isinstance(value, dict):
		return {k: _clean(v) for k, v in value.items() if k != "flags"}
	if isinstance(value, list):
		return [_clean(v) for v in value]
	return value


@tool("create_record")
def create_record(doctype, data):
	doc = frappe.client.insert({**_clean(data), "doctype": doctype})
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
	doc.update({k: v for k, v in _clean(data).items() if k not in ("doctype", "name")})
	doc.save()
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
