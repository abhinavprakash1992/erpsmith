"""DocType tools.

A custom DocType (custom=1) lives only in the database and is edited directly. A standard DocType ships with an
app: on a site in developer mode, deleting or renaming one also deletes or moves the app's source files, so these
tools refuse it. Standard DocTypes change through Custom Fields and Property Setters instead.
"""

import re

import frappe

from erpsmith.registry import InvalidArguments, required_text, tool

DEFAULT_SERIES = "CUSTOM-.YYYY.-.#####"
PERM_COLUMNS = ["role", "permlevel", "if_owner", "read", "write", "create", "delete", "submit", "cancel", "amend",
	"report", "export", "import", "print", "email", "share"]
FULL_ACCESS = {"read": 1, "write": 1, "create": 1, "delete": 1, "report": 1, "export": 1, "print": 1, "email": 1, "share": 1}
# Frappe's own bookkeeping for a DocType, which deleting the DocType leaves behind
LEFTOVERS = (("Custom Field", "dt"), ("Property Setter", "doc_type"), ("Custom DocPerm", "parent"))


class StandardDocType(frappe.ValidationError):
	pass


def to_fieldname(label):
	return re.sub(r"[^a-z0-9]+", "_", (label or "").lower()).strip("_")


def docfield(spec):
	"""A DocField or Custom Field dict from a tool's field spec (the Node server's camelCase shape)."""
	if not isinstance(spec, dict):
		raise InvalidArguments("each field must be an object")
	fieldname = spec.get("fieldname") or to_fieldname(spec.get("label"))
	if not fieldname:
		raise InvalidArguments("each field needs a label or a fieldname")
	row = {
		"fieldname": fieldname,
		"label": spec.get("label"),
		"fieldtype": spec.get("fieldtype") or "Data",
		"reqd": int(bool(spec.get("required"))),
		"bold": int(bool(spec.get("bold"))),
		"in_list_view": int(bool(spec.get("inListView"))),
		"in_standard_filter": int(bool(spec.get("inFilter"))),
		"read_only": int(bool(spec.get("readOnly"))),
		"hidden": int(bool(spec.get("hidden"))),
	}
	for key, target in (("options", "options"), ("default", "default"), ("description", "description"),
			("dependsOn", "depends_on"), ("mandatoryDependsOn", "mandatory_depends_on")):
		if spec.get(key) not in (None, ""):
			row[target] = spec[key]
	return row


def is_custom(doctype):
	custom = frappe.db.get_value("DocType", required_text("doctype", doctype), "custom")
	if custom is None:
		raise frappe.DoesNotExistError(f"DocType {doctype} not found")
	return bool(custom)


def refuse_standard(doctype, action):
	if not is_custom(doctype):
		raise StandardDocType(
			f"{doctype} is a standard DocType (it ships with an app), so it cannot be {action} here. "
			"Standard DocTypes change through Custom Fields and Property Setters: add_field, update_field, set_property.")


def permission_rules(doctype):
	"""(source, rules): once any Custom DocPerm row exists for a DocType, those rows replace its own DocPerm rows."""
	custom = frappe.get_all("Custom DocPerm", filters={"parent": doctype}, fields=PERM_COLUMNS, order_by="permlevel, role")
	if custom:
		return "Custom DocPerm", custom
	return "DocPerm", frappe.get_all("DocPerm", filters={"parent": doctype, "parenttype": "DocType"},
		fields=PERM_COLUMNS, order_by="permlevel, role")


def field_summary(df):
	return {"fieldname": df.fieldname, "label": df.label, "fieldtype": df.fieldtype, "options": df.options,
		"reqd": df.reqd, "hidden": df.hidden, "read_only": df.read_only, "in_list_view": df.in_list_view,
		"default": df.default, "depends_on": df.depends_on, "custom": bool(df.get("is_custom_field"))}


@tool("create_doctype")
def create_doctype(name, module, fields=None, isSubmittable=False, isChild=False, isSingle=False, hasNamingSeries=True,
		namingSeries=None, autoName=None, trackChanges=True, description=None, icon=None):
	if fields is not None and not isinstance(fields, list):
		raise InvalidArguments("fields must be a list of field objects")
	rows = [docfield(f) for f in fields or []]
	if isSingle or isChild:
		autoname = autoName or ("" if isSingle else "hash")
	else:
		autoname = autoName or ("naming_series:" if hasNamingSeries else "hash")
	if autoname == "naming_series:" and not any(r["fieldname"] == "naming_series" for r in rows):
		series = namingSeries or DEFAULT_SERIES
		rows.insert(0, {"fieldname": "naming_series", "label": "Series", "fieldtype": "Select", "options": series,
			"default": series, "reqd": 1})
	perms = [] if isChild else [{"role": "System Manager", **FULL_ACCESS,
		**({"submit": 1, "cancel": 1, "amend": 1} if isSubmittable else {})}]
	doc = frappe.get_doc({
		"doctype": "DocType",
		"name": required_text("name", name),
		"module": required_text("module", module),
		"custom": 1,  # stored in the database only: works without developer mode and never writes app files
		"is_submittable": int(bool(isSubmittable)),
		"istable": int(bool(isChild)),
		"issingle": int(bool(isSingle)),
		"track_changes": int(bool(trackChanges)),
		"autoname": autoname,
		"fields": rows,
		"permissions": perms,
	})
	if description:
		doc.description = description
	if icon:
		doc.icon = icon
	doc.insert()
	return {"doctype": doc.name, "module": doc.module, "custom": True, "autoname": doc.autoname,
		"fields": [f.fieldname for f in doc.fields]}


@tool("get_doctype")
def get_doctype(name):
	meta = frappe.get_meta(required_text("name", name))
	source, rules = permission_rules(meta.name)
	return {
		"name": meta.name, "module": meta.module, "custom": bool(meta.custom),
		"isSubmittable": bool(meta.is_submittable), "isChild": bool(meta.istable), "isSingle": bool(meta.issingle),
		"autoname": meta.autoname, "fieldCount": len(meta.fields),
		"fields": [field_summary(df) for df in meta.fields],
		"permissionSource": source, "permissions": rules,
	}


@tool("list_doctypes")
def list_doctypes(module=None, search=None):
	filters = []
	if module:
		filters.append(["module", "=", module])
	if search:
		filters.append(["name", "like", f"%{search}%"])
	rows = frappe.get_list("DocType", filters=filters, order_by="name asc", limit_page_length=200,
		fields=["name", "module", "custom", "istable", "issingle", "is_submittable", "description"])
	return {"count": len(rows), "doctypes": rows}


@tool("delete_doctype")
def delete_doctype(name):
	refuse_standard(name, "deleted")
	frappe.delete_doc("DocType", name)
	for doctype, field in LEFTOVERS:
		frappe.db.delete(doctype, {field: name})
	return {"deleted": name, "table": f"Frappe keeps the table `tab{name}` and its rows; "
		"`bench --site <site> trim-database` drops the tables of deleted DocTypes."}


@tool("rename_doctype")
def rename_doctype(oldName, newName):
	refuse_standard(oldName, "renamed")
	new = frappe.rename_doc("DocType", oldName, required_text("newName", newName))
	return {"renamed": True, "from": oldName, "to": new}
