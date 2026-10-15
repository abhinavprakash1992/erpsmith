"""DocType tools.

A custom DocType (custom=1) lives only in the database and is edited directly. A standard DocType ships with an
app: on a site in developer mode, deleting or renaming one also deletes or moves the app's source files, so these
tools refuse it. Standard DocTypes change through Custom Fields and Property Setters instead.
"""

import json
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


def _field(doctype, fieldname):
	df = frappe.get_meta(required_text("doctype", doctype)).get_field(required_text("fieldname", fieldname))
	if not df:
		raise InvalidArguments(f"{doctype} has no field {fieldname}; list_fields shows its fields")
	return df


def _custom_field(doctype, fieldname):
	return frappe.db.get_value("Custom Field", {"dt": doctype, "fieldname": fieldname})


def _property_type(level, prop):
	"""A Property Setter records the type of the property it overrides: that property's fieldtype on DocField or DocType."""
	df = frappe.get_meta(level).get_field(prop)
	if not df:
		raise InvalidArguments(f"{prop} is not a {'field' if level == 'DocField' else 'DocType'} property")
	return df.fieldtype


def property_setter(doctype, fieldname, prop, value):
	"""Override one property of a DocType (fieldname None) or of one of its fields. Frappe deletes the setter it replaces."""
	level = "DocField" if fieldname else "DocType"
	frappe.get_doc({
		"doctype": "Property Setter", "doctype_or_field": level, "doc_type": doctype, "field_name": fieldname,
		"property": prop, "property_type": _property_type(level, prop),
		"value": str(int(value)) if isinstance(value, bool) else "" if value is None else str(value),
	}).insert()


def change_field(doctype, fieldname, updates):
	"""Set field properties where they belong: the Custom Field, the custom DocType itself, or Property Setters
	on a standard field. Returns which of the three it used."""
	if not isinstance(updates, dict) or not updates:
		raise InvalidArguments("updates must be a non-empty object of field properties")
	df = _field(doctype, fieldname)
	if "fieldname" in updates:
		raise InvalidArguments("a fieldname cannot be changed; add_field a new field and remove_field the old one")
	properties = frappe.get_meta("DocField")
	unknown = sorted(k for k in updates if not properties.get_field(k))
	if unknown:
		raise InvalidArguments(f"not field properties: {', '.join(unknown)}")
	custom_field = _custom_field(doctype, df.fieldname)
	if custom_field:
		doc = frappe.get_doc("Custom Field", custom_field)
		doc.update(updates)
		doc.save()
		return "Custom Field"
	if is_custom(doctype):
		doc = frappe.get_doc("DocType", doctype)
		next(row for row in doc.fields if row.fieldname == df.fieldname).update(updates)
		doc.save()
		return "DocType"
	for prop, value in updates.items():
		property_setter(doctype, df.fieldname, prop, value)
	return "Property Setter"


@tool("add_field")
def add_field(doctype, label, fieldtype, fieldname=None, options=None, required=False, bold=False, inListView=False,
		inFilter=False, readOnly=False, hidden=False, default=None, description=None, dependsOn=None, insertAfter=None,
		mandatoryDependsOn=None):
	row = docfield({"label": label, "fieldtype": fieldtype, "fieldname": fieldname, "options": options, "required": required,
		"bold": bold, "inListView": inListView, "inFilter": inFilter, "readOnly": readOnly, "hidden": hidden,
		"default": default, "description": description, "dependsOn": dependsOn, "mandatoryDependsOn": mandatoryDependsOn})
	meta = frappe.get_meta(required_text("doctype", doctype))
	if insertAfter and not meta.get_field(insertAfter):
		raise InvalidArguments(f"{doctype} has no field {insertAfter} to insert after")
	if is_custom(doctype):
		doc = frappe.get_doc("DocType", doctype)
		new = doc.append("fields", row)
		if insertAfter:
			doc.fields.remove(new)
			doc.fields.insert(next(i for i, r in enumerate(doc.fields) if r.fieldname == insertAfter) + 1, new)
			for i, r in enumerate(doc.fields, 1):
				r.idx = i
		doc.save()
		return {"doctype": doctype, "fieldname": row["fieldname"], "method": "DocType"}
	# A Custom Field is the upgrade-safe way to extend a standard DocType. Without insert_after it would go first.
	custom = frappe.get_doc({"doctype": "Custom Field", "dt": doctype, **row,
		"insert_after": insertAfter or (meta.fields[-1].fieldname if meta.fields else None)}).insert()
	return {"doctype": doctype, "fieldname": custom.fieldname, "method": "Custom Field"}


@tool("update_field")
def update_field(doctype, fieldname, updates):
	method = change_field(doctype, fieldname, updates)
	return {"updated": True, "doctype": doctype, "fieldname": fieldname, "method": method, "properties": sorted(updates)}


@tool("remove_field")
def remove_field(doctype, fieldname):
	df = _field(doctype, fieldname)
	custom_field = _custom_field(doctype, df.fieldname)
	if custom_field:
		frappe.delete_doc("Custom Field", custom_field)
		method = "Custom Field"
	elif is_custom(doctype):
		doc = frappe.get_doc("DocType", doctype)
		doc.fields = [row for row in doc.fields if row.fieldname != df.fieldname]
		doc.save()
		method = "DocType"
	else:
		raise StandardDocType(f"{doctype} is a standard DocType and {df.fieldname} is one of its own fields, so it cannot "
			"be removed here; set_property with property hidden and value 1 hides it.")
	return {"removed": df.fieldname, "doctype": doctype, "method": method}


@tool("list_fields")
def list_fields(doctype):
	fields = frappe.get_meta(required_text("doctype", doctype)).fields
	return {"doctype": doctype, "count": len(fields), "fields": [field_summary(df) for df in fields]}


@tool("reorder_fields")
def reorder_fields(doctype, fieldOrder):
	if not isinstance(fieldOrder, list) or not all(isinstance(f, str) for f in fieldOrder):
		raise InvalidArguments("fieldOrder must be a list of fieldnames")
	current = [df.fieldname for df in frappe.get_meta(required_text("doctype", doctype)).fields]
	wanted = [f for f in dict.fromkeys(fieldOrder) if f in current]
	# Frappe ignores a field_order that does not name every field, so the fields not listed keep their order after these.
	order = wanted + [f for f in current if f not in wanted]
	frappe.get_doc({"doctype": "Property Setter", "doctype_or_field": "DocType", "doc_type": doctype,
		"property": "field_order", "property_type": "Data", "value": json.dumps(order)}).insert()
	return {"doctype": doctype, "reordered": len(wanted), "ignored": [f for f in fieldOrder if f not in current], "order": order}


@tool("set_property")
def set_property(doctype, property, value, fieldname=None):
	frappe.get_meta(required_text("doctype", doctype))
	if fieldname:
		_field(doctype, fieldname)
	property_setter(doctype, fieldname or None, required_text("property", property), value)
	return {"set": True, "doctype": doctype, "fieldname": fieldname, "property": property, "value": value}
