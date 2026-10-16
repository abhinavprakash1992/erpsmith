"""Builder tools: workflows, permission rules, print formats, reports, notifications and dashboard charts."""

import json

import frappe
from frappe.core.doctype.doctype.doctype import validate_permissions_for_doctype
from frappe.permissions import add_permission, update_permission_property

from erpsmith import sanitizer
from erpsmith.registry import InvalidArguments, required_text, tool
from erpsmith.tools.doctype import PERM_COLUMNS
from erpsmith.tools.scripts import require_server_scripts

PERM_DEFAULTS = {"read": 1, "write": 0, "create": 0, "delete": 0, "submit": 0, "cancel": 0, "amend": 0,
	"report": 1, "export": 1, "import": 0, "print": 1, "email": 1, "share": 1}


def _objects(name, value, required=()):
	"""`value` as a list of dicts that each have the `required` keys."""
	if value is None:
		return []
	if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
		raise InvalidArguments(f"{name} must be a list of objects")
	for v in value:
		missing = [k for k in required if not v.get(k)]
		if missing:
			raise InvalidArguments(f"each item of {name} needs {', '.join(required)}; one is missing {', '.join(missing)}")
	return value


def _ensure(doctype, name, data):
	"""Create the master record `name` unless it exists. Returns True when it was created."""
	if frappe.db.exists(doctype, name):
		return False
	frappe.get_doc({"doctype": doctype, **data}).insert()
	return True


def grant(doctype, role, permissions):
	"""Set `role`'s level-0 permission rule on `doctype` the way the Role Permission Manager does. Frappe ignores a
	DocType's own rules once any Custom DocPerm row exists for it, so add_permission first copies them into Custom
	DocPerm: every other role keeps its access."""
	# The Role Permission Manager's rule. Not frappe.only_for: it lets everyone through while tests run on v15.
	if "System Manager" not in frappe.get_roles():
		raise frappe.PermissionError("Changing permission rules needs the System Manager role")
	if permissions is not None and not isinstance(permissions, dict):
		raise InvalidArguments("permissions must be an object such as {\"read\": 1, \"write\": 1}")
	unknown = sorted(set(permissions or {}) - set(PERM_DEFAULTS))
	if unknown:
		raise InvalidArguments(f"unknown permission types: {', '.join(unknown)}. Known: {', '.join(PERM_DEFAULTS)}")
	frappe.get_meta(required_text("doctype", doctype))  # DoesNotExistError for an unknown DocType
	if not frappe.db.exists("Role", required_text("role", role)):
		raise InvalidArguments(f"Role {role} does not exist")
	values = {**PERM_DEFAULTS, **{k: int(bool(v)) for k, v in (permissions or {}).items()}}
	add_permission(doctype, role, 0)  # does nothing if the rule exists
	for ptype, value in values.items():
		update_permission_property(doctype, role, 0, ptype, value, validate=False)
	validate_permissions_for_doctype(doctype)
	frappe.clear_cache(doctype=doctype)
	rule = frappe.get_all("Custom DocPerm", filters={"parent": doctype, "role": role, "permlevel": 0, "if_owner": 0},
		fields=PERM_COLUMNS)[0]
	return {"doctype": doctype, "role": role, "permissions": {k: rule[k] for k in values}}


@tool("create_workflow")
def create_workflow(name, doctype, states, transitions, sendEmailAlerts=False):
	states = _objects("states", states, ("state",))
	transitions = _objects("transitions", transitions, ("from", "action", "to"))
	if not states:
		raise InvalidArguments("states must list at least one state")
	# Workflow rows link to Workflow State and Workflow Action Master records; Workflow Builder creates missing ones too.
	created_states = [s["state"] for s in states
		if _ensure("Workflow State", s["state"], {"workflow_state_name": s["state"], "style": s.get("style") or ""})]
	created_actions = [a for a in dict.fromkeys(t["action"] for t in transitions)
		if _ensure("Workflow Action Master", a, {"workflow_action_name": a})]
	doc = frappe.get_doc({
		"doctype": "Workflow",
		"workflow_name": required_text("name", name),
		"document_type": required_text("doctype", doctype),
		"workflow_state_field": "workflow_state",
		"is_active": 1,
		"send_email_alert": int(bool(sendEmailAlerts)),
		"states": [{"state": s["state"], "doc_status": str(s.get("docStatus") or "0"),
			"allow_edit": s.get("allowEdit") or "Administrator", "is_optional_state": int(bool(s.get("optional")))} for s in states],
		"transitions": [{"state": t["from"], "action": t["action"], "next_state": t["to"], "allowed": t.get("allowed") or "All",
			"allow_self_approval": int(bool(t.get("allowSelfApproval")))} for t in transitions],
	}).insert()
	return {"name": doc.name, "doctype": doc.document_type, "states": len(states), "transitions": len(transitions),
		"createdStates": created_states, "createdActions": created_actions}


@tool("list_workflows")
def list_workflows(doctype=None):
	rows = frappe.get_list("Workflow", filters={"document_type": doctype} if doctype else {}, limit=50,
		fields=["name", "document_type", "is_active", "workflow_state_field", "modified"], order_by="modified desc")
	return {"count": len(rows), "workflows": rows}


@tool("set_permission")
def set_permission(doctype, role, permissions=None):
	return grant(doctype, role, permissions)


@tool("create_print_format")
def create_print_format(name, doctype, htmlContent, cssContent=None):
	doc = frappe.get_doc({
		"doctype": "Print Format", "name": required_text("name", name), "doc_type": required_text("doctype", doctype),
		"module": "Custom", "standard": "No",
		"custom_format": 1,  # without it Frappe renders the builder layout and ignores the HTML
		"print_format_type": "Jinja", "html": required_text("htmlContent", htmlContent), "css": cssContent or "",
	}).insert()
	return {"name": doc.name, "doctype": doc.doc_type}


@tool("create_report")
def create_report(name, doctype, reportType="Script Report", script=None, query=None, columns=None, filters=None):
	data = {
		"doctype": "Report", "report_name": required_text("name", name), "ref_doctype": required_text("doctype", doctype),
		"report_type": reportType, "is_standard": "No", "module": "Custom",
		"columns": [{"fieldname": c.get("fieldname"), "label": c.get("label"), "fieldtype": c.get("fieldtype") or "Data",
			"options": c.get("options"), "width": c.get("width") or 100} for c in _objects("columns", columns, ("fieldname",))],
		"filters": [{"fieldname": f.get("fieldname"), "label": f.get("label"), "fieldtype": f.get("fieldtype") or "Data",
			"options": f.get("options"), "mandatory": int(bool(f.get("mandatory"))), "default": f.get("default")}
			for f in _objects("filters", filters, ("fieldname",))],
	}
	if reportType == "Script Report":
		require_server_scripts()  # a non-standard Script Report runs in the same sandbox as a Server Script
		data["report_script"], _ = sanitizer.clean_or_raise(required_text("script", script), "Script Report")
	elif reportType == "Query Report":
		data["query"] = required_text("query", query)
	doc = frappe.get_doc(data).insert()
	return {"name": doc.name, "doctype": doc.ref_doctype, "reportType": doc.report_type}


@tool("create_notification")
def create_notification(name, doctype, subject, message, event="New", condition=None, recipients=None, channels=None,
		valueChanged=None, dateChanged=None, daysInAdvance=None, method=None):
	if channels is not None and (not isinstance(channels, list) or len(channels) != 1):
		raise InvalidArguments("channels takes exactly one channel; a Frappe Notification has one, so make one per channel")
	doc = frappe.get_doc({
		"doctype": "Notification", "name": required_text("name", name), "document_type": required_text("doctype", doctype),
		"event": event or "New", "channel": (channels or ["Email"])[0], "enabled": 1, "is_standard": 0,
		"subject": required_text("subject", subject), "message": required_text("message", message), "condition": condition,
		"value_changed": valueChanged, "date_changed": dateChanged, "days_in_advance": daysInAdvance, "method": method,
		"recipients": [{"receiver_by_document_field": r.get("field"), "receiver_by_role": r.get("role"), "cc": r.get("email"),
			"condition": r.get("condition")} for r in _objects("recipients", recipients)],
	}).insert()
	return {"name": doc.name, "doctype": doc.document_type, "event": doc.event, "channel": doc.channel}


@tool("create_dashboard_chart")
def create_dashboard_chart(name, doctype, chartType="Bar", fieldName=None, groupBy=None, filters=None, timeSpan=None, timeSeries=None):
	if filters is not None and not isinstance(filters, list):
		raise InvalidArguments("filters must be a list of [fieldname, operator, value] filters")
	data = {"doctype": "Dashboard Chart", "chart_name": required_text("name", name), "document_type": required_text("doctype", doctype),
		"type": chartType or "Bar", "filters_json": json.dumps(filters or []), "is_public": 1}
	if groupBy:
		data.update(chart_type="Group By", group_by_based_on=groupBy, group_by_type="Count")
	else:  # a Count chart is always a time series, so timeSeries changes nothing
		data.update(chart_type="Count", based_on=fieldName or "creation", timeseries=1, timespan=timeSpan or "Last Year",
			time_interval="Monthly")
	doc = frappe.get_doc(data).insert()
	return {"name": doc.name, "doctype": doc.document_type, "chartType": doc.type, "basis": doc.chart_type}
