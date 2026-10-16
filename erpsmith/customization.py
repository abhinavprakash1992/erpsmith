"""Knowledge chunks for this site's own wiring: workflows, scripts, notifications and print formats.

`customization_chunks` builds one chunk per row (content is what a search returns, summary is what is
embedded); `attachments` gives each DocType the one-line list of what is attached to it. Script code goes into a
chunk only after `scrub.scrub`. Notification message bodies, literal recipient addresses and print format HTML
stay out: only role and field names, and the `doc.<field>` names those texts use.
"""

import re
from collections import defaultdict
from urllib.parse import quote

import frappe

from erpsmith.scrub import scrub

DOC_FIELDS = re.compile(r"\bdoc\.(\w+)")
ASSIGNED = re.compile(r"\bdoc\.(\w+)\s*=(?!=)")
NAMED_DOCTYPES = re.compile(r"""\b(?:get_doc|get_all|get_list|get_value|new_doc)\(\s*["']([^"'\n]+)["']""")
HANDLERS = re.compile(r"(\w+)\s*(?::\s*function)?\s*\(\s*frm\b")
NOT_HANDLERS = frozenset({"function", "if", "while", "for"})


def _rows(doctype, fields, **kwargs):
	"""All rows of `doctype` (the build runs as a background job, so no permission check), or nothing on a site without it."""
	if not frappe.db.table_exists(doctype):
		return []
	return frappe.get_all(doctype, fields=fields, order_by="name asc", **kwargs)


def _children(child, parent, fields):
	"""{parent name: [child rows]} for a child table, in row order."""
	grouped = defaultdict(list)
	if frappe.db.table_exists(child):
		for row in frappe.get_all(child, filters={"parenttype": parent}, fields=["parent", *fields], order_by="idx asc"):
			grouped[row.parent].append(row)
	return grouped


def _unique(items):
	return list(dict.fromkeys(i for i in items if i))


def _list(items):
	return ", ".join(items) if items else "none"


def _chunk(doctype, name, summary, content):
	route = frappe.scrub(doctype).replace("_", "-")
	return {"kind": "customization", "chunk_key": f"customization::{doctype}::{name}", "title": name,
		"source": f"/app/{route}/{quote(name)}", "summary": summary, "content": content}


WORKFLOW = ["name", "document_type", "is_active", "workflow_state_field"]
SERVER_SCRIPT = ["name", "script_type", "reference_doctype", "doctype_event", "api_method", "allow_guest", "event_frequency",
	"cron_format", "module", "disabled", "script"]
CLIENT_SCRIPT = ["name", "dt", "view", "enabled", "module", "script"]
NOTIFICATION = ["name", "document_type", "event", "value_changed", "date_changed", "days_in_advance", "channel", "condition",
	"subject", "message", "enabled"]
PRINT_FORMAT = ["name", "doc_type", "print_format_type", "print_format_builder", "print_format_builder_beta", "disabled", "html"]


def _notifications():
	return _rows("Notification", NOTIFICATION, filters={"is_standard": 0})


def _print_formats():
	return _rows("Print Format", PRINT_FORMAT, filters={"standard": "No"})


def _default_formats():
	"""{(DocType, print format)} that a Property Setter makes the default."""
	return {(p.doc_type, p.value) for p in _rows("Property Setter", ["doc_type", "value"], filters={"property": "default_print_format"})}


def _workflow_chunks():
	states = _children("Workflow Document State", "Workflow", ["state", "doc_status", "allow_edit", "update_field", "update_value"])
	moves = _children("Workflow Transition", "Workflow", ["state", "action", "next_state", "allowed", "condition"])
	for w in _rows("Workflow", WORKFLOW):
		state_lines = [f"- {s.state} (docstatus {s.doc_status or 0}; edited by {s.allow_edit or 'nobody'}"
			+ (f"; sets {s.update_field} = {scrub(s.update_value)}" if s.update_field else "") + ")" for s in states[w.name]]
		move_lines = [f"{t.state} --{t.action} ({t.allowed}{', ' + scrub(t.condition) if t.condition else ''})--> {t.next_state}"
			for t in moves[w.name]]
		active = "active" if w.is_active else "inactive"
		content = "\n".join([f"Workflow: {w.name}", f"DocType: {w.document_type} | {active} | State field: {w.workflow_state_field}",
			"States:", *state_lines, "Transitions:", *move_lines])
		summary = (f"Workflow {w.name} on {w.document_type} ({active}): states {_list([s.state for s in states[w.name]])}; "
			+ "; ".join(f"{t.state} → {t.next_state} by {t.allowed}" for t in moves[w.name]) + ".")
		yield _chunk("Workflow", w.name, summary, content)


def _server_script_chunks():
	for s in _rows("Server Script", SERVER_SCRIPT):
		code = scrub(s.script)
		target = (s.reference_doctype + (f", event {s.doctype_event}" if s.doctype_event else "") if s.reference_doctype
			else f"API method {s.api_method}" if s.api_method
			else f"schedule {s.cron_format or s.event_frequency}" if s.script_type == "Scheduler Event" else "")
		named, fields = _unique(NAMED_DOCTYPES.findall(code)), _unique(ASSIGNED.findall(code))
		content = "\n".join(part for part in [f"Server Script: {s.name}{' [disabled]' if s.disabled else ''}", f"Type: {s.script_type}",
			target and f"Target: {target}", s.api_method and f"Guest access: {'yes' if s.allow_guest else 'no'}",
			s.module and f"Module: {s.module}", "Code:", code] if part)
		summary = " ".join(part for part in [f"Server Script {s.name} ({s.script_type})" + (f" on {target}" if target else "")
			+ (", disabled" if s.disabled else "") + ".", named and f"Names DocTypes: {', '.join(named)}.",
			fields and f"Assigns fields: {', '.join(fields)}."] if part)
		yield _chunk("Server Script", s.name, summary, content)


def _client_script_chunks():
	for c in _rows("Client Script", CLIENT_SCRIPT):
		code = scrub(c.script)
		handlers = _unique(h for h in HANDLERS.findall(code) if h not in NOT_HANDLERS)
		content = "\n".join(part for part in [f"Client Script: {c.name}{'' if c.enabled else ' [disabled]'}",
			f"DocType: {c.dt} | View: {c.view}", c.module and f"Module: {c.module}", "Code:", code] if part)
		summary = (f"Client Script {c.name} on {c.dt} ({c.view} view{'' if c.enabled else ', disabled'})."
			+ (f" Handlers: {', '.join(handlers)}." if handlers else ""))
		yield _chunk("Client Script", c.name, summary, content)


def _notification_chunks():
	recipients = _children("Notification Recipient", "Notification", ["receiver_by_role", "receiver_by_document_field"])
	for n in _notifications():
		roles = _unique(r.receiver_by_role for r in recipients[n.name])
		by_field = _unique(r.receiver_by_document_field for r in recipients[n.name])
		used = _unique(DOC_FIELDS.findall(n.message or ""))
		when = n.event + (f" of {n.value_changed}" if n.value_changed else "") \
			+ (f" ({n.days_in_advance} days, date {n.date_changed})" if n.date_changed else "")
		content = "\n".join(part for part in [f"Notification: {n.name}{'' if n.enabled else ' [disabled]'}",
			f"DocType: {n.document_type} | Event: {when} | Channel: {n.channel}", n.condition and f"Condition: {scrub(n.condition)}",
			f"Recipients by role: {_list(roles)}", f"Recipients by document field: {_list(by_field)}",
			n.subject and f"Subject: {scrub(n.subject)}", f"Message uses fields: {_list(used)}"] if part)
		summary = (f"Notification {n.name} on {n.document_type} fires on {when} through {n.channel}"
			+ ("" if n.enabled else " (disabled)") + f"; goes to roles {_list(roles)} and document fields {_list(by_field)}; "
			f"its message uses {_list(used)}.")
		yield _chunk("Notification", n.name, summary, content)


def _print_format_chunks():
	defaults = _default_formats()
	for p in _print_formats():
		kind = "builder" if p.print_format_builder or p.print_format_builder_beta else p.print_format_type or "Jinja"
		is_default = (p.doc_type, p.name) in defaults
		used = _unique(DOC_FIELDS.findall(p.html or ""))
		flags = [f for f, on in (("default", is_default), ("disabled", p.disabled)) if on]
		content = "\n".join([f"Print Format: {p.name}{' [disabled]' if p.disabled else ''}",
			f"DocType: {p.doc_type} | Type: {kind} | Default: {'yes' if is_default else 'no'}", f"Prints fields: {_list(used)}"])
		summary = f"Print Format {p.name} on {p.doc_type} ({', '.join([kind, *flags])}); prints {_list(used)}."
		yield _chunk("Print Format", p.name, summary, content)


def _guarded(doctype, build):
	"""build() for one source DocType; a failure (one bad row) is logged and costs only that source."""
	try:
		return build()
	except Exception:
		frappe.log_error(title=f"ERPSmith: customization chunks for {doctype} failed")
		return None


def customization_chunks():
	"""One chunk per workflow, server script, client script, custom notification and custom print format on this site."""
	chunks = []
	for doctype, source in (("Workflow", _workflow_chunks), ("Server Script", _server_script_chunks),
			("Client Script", _client_script_chunks), ("Notification", _notification_chunks), ("Print Format", _print_format_chunks)):
		chunks += _guarded(doctype, lambda: list(source())) or []
	return chunks


def _attach_workflows(parts):
	for w in _rows("Workflow", WORKFLOW):
		parts[w.document_type]["Workflow"].append(f'"{w.name}" ({"active" if w.is_active else "inactive"})')


def _attach_server_scripts(parts):
	for s in _rows("Server Script", SERVER_SCRIPT[:4] + ["disabled"]):
		if s.reference_doctype:
			parts[s.reference_doctype]["Server Scripts"].append(
				f"{s.name} ({', '.join(part for part in [s.doctype_event or s.script_type, s.disabled and 'disabled'] if part)})")


def _attach_client_scripts(parts):
	for c in _rows("Client Script", ["name", "dt", "view", "enabled"]):
		parts[c.dt]["Client Scripts"].append(f"{c.name} ({', '.join(part for part in [c.view, not c.enabled and 'disabled'] if part)})")


def _attach_notifications(parts):
	for n in _notifications():
		parts[n.document_type]["Notifications"].append(f"{n.name} ({', '.join(part for part in [n.event, not n.enabled and 'disabled'] if part)})")


def _attach_print_formats(parts):
	defaults = _default_formats()
	for p in _print_formats():
		flags = ", ".join(f for f, on in (("default", (p.doc_type, p.name) in defaults), ("disabled", p.disabled)) if on)
		parts[p.doc_type]["Print Formats"].append(f"{p.name} ({flags})" if flags else p.name)


def attachments():
	"""{DocType: 'Customizations: …'}, the one line a DocType chunk shows of what is attached to it."""
	parts = defaultdict(lambda: defaultdict(list))  # DocType -> section -> entries
	for doctype, attach in (("Workflow", _attach_workflows), ("Server Script", _attach_server_scripts),
			("Client Script", _attach_client_scripts), ("Notification", _attach_notifications), ("Print Format", _attach_print_formats)):
		_guarded(doctype, lambda: attach(parts))
	return {dt: "Customizations: " + "; ".join(
		"; ".join(f"Workflow {w}" for w in items) if section == "Workflow" else f"{section}: {', '.join(items)}"
		for section, items in sections.items()) for dt, sections in parts.items()}
