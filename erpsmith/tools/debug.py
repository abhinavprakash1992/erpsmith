"""Read-only diagnosis: scripts, DocType fields, workflows, permissions, data integrity, error and scheduler logs.

A port of the Node server's debugger.js checks, with these changes: server scripts are checked against the sandbox's
real limits (erpsmith.sanitizer) instead of a list of import patterns; permission checks read the rules in force
(Custom DocPerm rows replace a DocType's own); linked records are looked up with frappe.db.exists, so a record the
user cannot read is not reported as deleted; and child tables are not reported for having no permission rules.
"""

import re

import frappe
import frappe.client
from frappe.utils import get_datetime, safe_exec

from erpsmith import sanitizer
from erpsmith.registry import InvalidArguments, clamp, required_text, tool
from erpsmith.tools.doctype import permission_rules
from erpsmith.tools.scripts import SCRIPTS_DISABLED

KNOWN_EVENTS = ("refresh", "onload", "before_save", "after_save", "validate", "submit", "cancel")
DEPRECATED_JS = (
	("locals[", "locals[] is deprecated; frm.doc or frappe.get_doc(doctype, name) reads a document."),
	("cur_frm.cscript", "cur_frm.cscript is deprecated; frappe.ui.form.on() registers form events."),
	("wn.model", "wn.model is deprecated; frappe.model replaces it."),
)
LOG_TAIL = 1000  # characters of a traceback to return: the exception itself is at the end


def _issue(kind, severity, message, **extra):
	return {"type": kind, "severity": severity, "message": message, **extra}


def _status(issues):
	if any(i["severity"] == "error" for i in issues):
		return "errors"
	return "warnings" if issues else "clean"


def _report(issues, fixes, **fields):
	return {**fields, "issueCount": len(issues), "issues": issues, "fixes": fixes, "status": _status(issues)}


def _scripts(doctype, name_or_doctype, by_doctype, fields):
	"""The script named `name_or_doctype`, or else every script of that DocType (at most 20)."""
	if frappe.db.exists(doctype, name_or_doctype):
		return [frappe.client.get(doctype, name_or_doctype)]
	rows = frappe.get_list(doctype, filters={by_doctype: name_or_doctype}, fields=fields, limit_page_length=20)
	if not rows:
		raise frappe.DoesNotExistError(f"No {doctype} is named {name_or_doctype} or belongs to that DocType")
	return rows


def check_client_script(row):
	script, issues, fixes = row.get("script") or "", [], []
	if row.get("view") == "Form" and "frappe.ui.form.on" not in script:
		issues.append(_issue("missing_wrapper", "error", "The script is not wrapped in frappe.ui.form.on(), so its events never fire.", line=1))
		body = "\n".join("  " + line for line in script.split("\n"))
		fixes.append({"type": "wrap_script", "description": "Wrap the script in frappe.ui.form.on()",
			"fixedScript": f"frappe.ui.form.on('{row['dt']}', {{\n{body}\n}});"})
	opening, closing = script.count("{"), script.count("}")
	if opening != closing:
		issues.append(_issue("syntax_unbalanced_braces", "error", f"Unbalanced braces: {opening} opening, {closing} closing."))
	console = [i for i, line in enumerate(script.split("\n"), 1) if "console.log" in line]
	if console:
		issues.append(_issue("console_log_found", "warning", f"console.log on lines {', '.join(map(str, console))}."))
	no_frm = [name for name in re.findall(r"(\w+)\s*\(\s*\)\s*\{", script) if name in KNOWN_EVENTS]
	if no_frm:
		issues.append(_issue("missing_frm_param", "error", f"Event handlers without the frm parameter: {', '.join(no_frm)}."))
	for pattern, message in DEPRECATED_JS:
		if pattern in script:
			issues.append(_issue("deprecated_api", "warning", message))
	if "cur_frm" in script:
		issues.append(_issue("cur_frm_usage", "warning", "cur_frm is unreliable; the frm parameter of the event handler is the form."))
	if not row.get("enabled"):
		issues.append(_issue("script_disabled", "warning", "The script is disabled and does not run."))
		fixes.append({"type": "enable_script", "description": "Enable the script", "tool": "update_client_script"})
	return _report(issues, fixes, name=row["name"], doctype=row["dt"], scriptType=row.get("view"), enabled=bool(row.get("enabled")))


def _recent_errors(script_name):
	"""Error Log rows whose traceback runs through this script (safe_exec names it '<serverscript>: <scrubbed name>')."""
	if not frappe.has_permission("Error Log", "read"):
		return []
	return frappe.get_list("Error Log", filters=[["error", "like", f"%<serverscript>: {frappe.scrub(script_name)}%"]],
		fields=["name", "creation", "error"], order_by="creation desc", limit_page_length=5)


def check_server_script(row):
	script, issues, fixes = row.get("script") or "", [], []
	for problem in sanitizer.check(script, row.get("script_type"))[2]:
		issues.append(_issue("sandbox", "error", problem))
	if "\t" in script and "    " in script:
		issues.append(_issue("mixed_indentation", "error", "Tabs and spaces are mixed in the indentation."))
		fixes.append({"type": "fix_indentation", "description": "Replace tabs with 4 spaces", "fixedScript": script.replace("\t", "    ")})
	if "frappe.db.sql" in script:
		issues.append(_issue("raw_sql", "warning", "Raw SQL: frappe.db.get_value and frappe.get_list avoid SQL injection."))
	if row.get("script_type") == "DocType Event" and "doc." not in script:
		issues.append(_issue("no_doc_reference", "warning", "The script never uses doc, the document the event is for."))
	if re.search(r"frappe\.throw\(\s*\)", script):
		issues.append(_issue("empty_throw", "error", "frappe.throw() without a message shows the user a blank error."))
	if "except:" in script and "except Exception" not in script:
		issues.append(_issue("bare_except", "warning", "A bare except: also catches the errors Frappe raises on purpose; except Exception is narrower."))
	errors = _recent_errors(row["name"])
	if errors:
		issues.append(_issue("previous_error", "error", f"{len(errors)} recent Error Log entries come from this script; the latest, "
			f"at {errors[0].creation}, ends: {(errors[0].error or '').strip()[-300:]}", errorLogs=[e.name for e in errors]))
	if row.get("disabled"):
		issues.append(_issue("script_disabled", "warning", "The script is disabled and does not run."))
		fixes.append({"type": "enable_script", "description": "Enable the script", "tool": "update_server_script"})
	return _report(issues, fixes, name=row["name"], scriptType=row.get("script_type"), doctype=row.get("reference_doctype"),
		event=row.get("doctype_event"), enabled=not row.get("disabled"))


@tool("debug_client_script")
def debug_client_script(nameOrDoctype):
	rows = _scripts("Client Script", required_text("nameOrDoctype", nameOrDoctype), "dt", ["name", "dt", "view", "script", "enabled"])
	results = [check_client_script(r) for r in rows]
	return {"scripts": results, "totalIssues": sum(r["issueCount"] for r in results)}


@tool("debug_server_script")
def debug_server_script(nameOrDoctype):
	rows = _scripts("Server Script", required_text("nameOrDoctype", nameOrDoctype), "reference_doctype",
		["name", "script_type", "reference_doctype", "doctype_event", "script", "disabled"])
	results = [check_server_script(r) for r in rows]
	out = {"scripts": results, "totalIssues": sum(r["issueCount"] for r in results)}
	if not safe_exec.is_safe_exec_enabled():
		out["bench"] = SCRIPTS_DISABLED
		out["totalIssues"] += 1
	return out


@tool("debug_doctype_fields")
def debug_doctype_fields(doctype):
	meta = frappe.get_meta(required_text("doctype", doctype))
	doctypes = set(frappe.get_all("DocType", pluck="name"))
	fields = meta.fields
	names = [df.fieldname for df in fields]
	issues, fixes = [], []
	for df in fields:
		if df.fieldtype == "Link" and df.options and df.options not in doctypes:
			issues.append(_issue("broken_link", "error", f"Link field {df.fieldname} points to {df.options}, which does not exist.", field=df.fieldname))
			fixes.append({"type": "fix_link_options", "field": df.fieldname, "tool": "fix_broken_link"})
		if df.fieldtype in ("Table", "Table MultiSelect") and df.options and df.options not in doctypes:
			issues.append(_issue("broken_child_table", "error", f"Table field {df.fieldname} uses child DocType {df.options}, which does not exist.", field=df.fieldname))
			fixes.append({"type": "fix_link_options", "field": df.fieldname, "tool": "fix_broken_link"})
		if df.fieldtype == "Select" and not (df.options or "").strip():
			issues.append(_issue("empty_select_options", "error", f"Select field {df.fieldname} has no options, so its dropdown is empty.", field=df.fieldname))
			fixes.append({"type": "add_select_options", "field": df.fieldname, "tool": "update_field"})
		for ref in re.findall(r"doc\.(\w+)", df.depends_on or ""):
			if ref not in names:
				issues.append(_issue("broken_depends_on", "error", f"depends_on of {df.fieldname} uses {ref}, which is not a field.", field=df.fieldname))
		if df.fieldtype == "Dynamic Link" and df.options and df.options not in names:
			issues.append(_issue("broken_dynamic_link", "error", f"Dynamic Link {df.fieldname} takes its DocType from {df.options}, which is not a field.", field=df.fieldname))
		if names.count(df.fieldname) > 1 and names.index(df.fieldname) == fields.index(df):
			issues.append(_issue("duplicate_fieldname", "error", f"Fieldname {df.fieldname} appears {names.count(df.fieldname)} times.", field=df.fieldname))
		if re.search(r"[^a-z0-9_]", df.fieldname or ""):
			issues.append(_issue("invalid_fieldname", "error", f"Fieldname {df.fieldname} has characters other than lowercase letters, digits and underscores.", field=df.fieldname))
			fixes.append({"type": "rename_fieldname", "field": df.fieldname, "suggestedName": re.sub(r"[^a-z0-9]+", "_", df.fieldname.lower()).strip("_")})
	if not fields:
		issues.append(_issue("no_fields", "error", "The DocType has no fields."))
	if meta.is_submittable and not any(r.submit for r in permission_rules(meta.name)[1]):
		issues.append(_issue("submittable_no_permission", "warning", "The DocType is submittable but no role may submit."))
		fixes.append({"type": "add_submit_permission", "tool": "set_permission"})
	return _report(issues, fixes, doctype=meta.name, module=meta.module, fieldCount=len(fields))


def _workflow_name(doctype_or_workflow):
	if frappe.db.exists("Workflow", doctype_or_workflow):
		return doctype_or_workflow
	name = frappe.db.get_value("Workflow", {"document_type": doctype_or_workflow}, "name", order_by="is_active desc, modified desc")
	if not name:
		raise frappe.DoesNotExistError(f"No workflow is named {doctype_or_workflow} or belongs to that DocType")
	return name


@tool("debug_workflow")
def debug_workflow(doctypeOrWorkflow):
	wf = frappe.get_doc("Workflow", _workflow_name(required_text("doctypeOrWorkflow", doctypeOrWorkflow)))
	wf.check_permission("read")
	states = [s.state for s in wf.states]
	issues, fixes = [], []
	if not wf.is_active:
		issues.append(_issue("workflow_inactive", "error", "The workflow is not active, so it applies to no documents."))
		fixes.append({"type": "activate_workflow", "tool": "activate_workflow"})
	if not states:
		issues.append(_issue("no_states", "error", "The workflow has no states."))
	if not wf.transitions:
		issues.append(_issue("no_transitions", "error", "The workflow has no transitions, so documents cannot move."))
	for t in wf.transitions:
		for end, state in (("from", t.state), ("to", t.next_state)):
			if state not in states:
				issues.append(_issue(f"transition_{end}_missing_state", "error", f"Transition {t.action} goes {end} state {state}, which is not one of the workflow's states."))
				fixes.append({"type": "add_missing_state", "stateName": state})
		if t.allowed == "All":
			issues.append(_issue("transition_open_to_all", "warning", f"Every user may take the action {t.action} ({t.state} to {t.next_state})."))
	for state in states:
		moves = [t for t in wf.transitions if state in (t.state, t.next_state)]
		if not moves and len(states) > 1:
			issues.append(_issue("orphan_state", "warning", f"No transition leads into or out of state {state}."))
	field = wf.workflow_state_field
	if frappe.get_meta(wf.document_type).get_field(field):
		rows = frappe.get_list(wf.document_type, filters=[["docstatus", "=", 0], [field, "is", "set"]], fields=["name", field],
			limit_page_length=100)
		stuck = [{"name": r.name, "state": r[field]} for r in rows if r[field] not in states]
		if stuck:
			issues.append(_issue("stuck_documents", "error", f"{len(stuck)} draft documents are in a state the workflow no longer has.", documents=stuck))
			fixes.append({"type": "reset_stuck_documents", "tool": "fix_stuck_document", "documents": stuck})
	return _report(issues, fixes, workflow=wf.name, doctype=wf.document_type, isActive=bool(wf.is_active),
		stateCount=len(states), transitionCount=len(wf.transitions))


@tool("debug_permissions")
def debug_permissions(doctype, role=None):
	meta = frappe.get_meta(required_text("doctype", doctype))
	source, rules = permission_rules(meta.name)
	issues, fixes = [], []
	if meta.istable:
		return _report(issues, fixes, doctype=meta.name, role=role or "all", permissionSource=source, permissions=rules,
			note="A child table has no permission rules of its own; its parent DocType's rules apply.")
	if not rules:
		issues.append(_issue("no_permissions", "error", "The DocType has no permission rules, so no role can open it."))
		fixes.append({"type": "add_default_permissions", "tool": "set_permission"})
	if role:
		mine = [r for r in rules if r.role == role]
		if not frappe.db.exists("Role", role):
			issues.append(_issue("role_not_found", "error", f"Role {role} does not exist."))
		if not any(r.read for r in mine):
			issues.append(_issue("role_no_read", "error", f"Role {role} cannot read {meta.name}."))
			fixes.append({"type": "grant_read", "role": role, "tool": "set_permission"})
		if not any(r.write for r in mine):
			issues.append(_issue("role_no_write", "warning", f"Role {role} cannot write {meta.name}."))
	if meta.is_submittable and not any(r.submit for r in rules):
		issues.append(_issue("submittable_no_submit_perm", "warning", "The DocType is submittable but no role may submit."))
		fixes.append({"type": "grant_submit", "role": "System Manager", "tool": "set_permission"})
	if rules and all(not r.write and not r.create for r in rules):
		issues.append(_issue("all_read_only", "warning", "Every rule is read-only, so nobody can create or edit records."))
	return _report(issues, fixes, doctype=meta.name, role=role or "all", permissionSource=source,
		permissions=[r for r in rules if not role or r.role == role])


@tool("debug_data_integrity")
def debug_data_integrity(doctype, limit=50):
	meta = frappe.get_meta(required_text("doctype", doctype))
	if meta.issingle or meta.istable:
		raise InvalidArguments(f"{meta.name} is a {'single' if meta.issingle else 'child table'} DocType; this scan reads the records of a regular one")
	doctypes = set(frappe.get_all("DocType", pluck="name"))
	links = [df for df in meta.fields if df.fieldtype == "Link" and df.options in doctypes and not df.is_virtual]
	required = [df for df in meta.fields if df.reqd and df.fieldtype not in frappe.model.no_value_fields and not df.is_virtual]
	columns = list(dict.fromkeys(["name", "docstatus", *(df.fieldname for df in links + required)]))[:25]
	records = frappe.get_list(meta.name, fields=columns, order_by="creation desc", limit_page_length=clamp("limit", limit, 50, 1, 200))
	checked_links = [df for df in links if df.fieldname in columns][:5]  # each value costs a query
	issues, seen = [], {}
	for record in records:
		for df in required:
			if df.fieldname in columns and record.get(df.fieldname) in (None, ""):
				issues.append(_issue("missing_required_value", "error", f"Required field {df.label or df.fieldname} is empty on {record.name}.",
					record=record.name, field=df.fieldname))
		for df in checked_links:
			value = record.get(df.fieldname)
			if value:
				key = (df.options, value)
				if key not in seen:
					seen[key] = bool(frappe.db.exists(df.options, value))
				if not seen[key]:
					issues.append(_issue("broken_link_value", "error", f"{df.fieldname} on {record.name} points to {value} in {df.options}, which no longer exists.",
						record=record.name, field=df.fieldname, linkedDoctype=df.options, linkedValue=value))
	summary = {}
	for issue in issues:
		summary[issue["type"]] = summary.get(issue["type"], 0) + 1
	return {"doctype": meta.name, "recordsChecked": len(records), "issueCount": len(issues), "summary": summary,
		"issues": issues[:100], "status": "errors" if issues else "clean"}


@tool("get_error_logs")
def get_error_logs(method=None, doctype=None, since=None, limit=20):
	filters = []
	if method:
		filters.append(["method", "like", f"%{method}%"])
	if doctype:
		filters.append(["reference_doctype", "=", doctype])
	if since:
		try:
			filters.append(["creation", ">", get_datetime(since)])
		except Exception:
			raise InvalidArguments("since must be a date such as 2024-03-01") from None
	rows = frappe.get_list("Error Log", filters=filters, order_by="creation desc", limit_page_length=clamp("limit", limit, 20, 1, 200),
		fields=["name", "method", "error", "creation", "reference_doctype", "reference_name"])
	return {"count": len(rows), "logs": [{"name": r.name, "method": r.method, "doctype": r.reference_doctype, "record": r.reference_name,
		"when": r.creation, "error": (r.error or "")[-LOG_TAIL:]} for r in rows]}


@tool("get_scheduler_logs")
def get_scheduler_logs(limit=20):
	rows = frappe.get_list("Scheduled Job Log", order_by="creation desc", limit_page_length=clamp("limit", limit, 20, 1, 200),
		fields=["name", "scheduled_job_type", "status", "creation", "details"])
	return {"count": len(rows), "logs": [{"name": r.name, "job": r.scheduled_job_type, "status": r.status, "when": r.creation,
		"details": (r.details or "")[-LOG_TAIL:]} for r in rows]}


@tool("full_health_check")
def full_health_check(doctype):
	meta = frappe.get_meta(required_text("doctype", doctype))  # an unknown DocType is one clear error, not five

	def run(check, *args):
		try:
			return check(*args)
		except Exception as e:
			return {"error": str(e) or type(e).__name__}

	workflow = frappe.db.get_value("Workflow", {"document_type": meta.name}, "name", order_by="is_active desc, modified desc")
	checks = {
		"fields": run(debug_doctype_fields, meta.name),
		"clientScripts": run(debug_client_script, meta.name) if frappe.db.exists("Client Script", {"dt": meta.name})
			else {"skipped": "No client scripts"},
		"serverScripts": run(debug_server_script, meta.name) if frappe.db.exists("Server Script", {"reference_doctype": meta.name})
			else {"skipped": "No server scripts"},
		"permissions": run(debug_permissions, meta.name),
		"workflow": run(debug_workflow, workflow) if workflow else {"skipped": "No workflow"},
	}
	total = sum(c.get("issueCount", c.get("totalIssues", 0)) for c in checks.values())
	return {"doctype": meta.name, "totalIssues": total, "status": "issues found" if total else "clean", "checks": checks}
