"""Client Script and Server Script tools. Server-side Python passes erpsmith.sanitizer before it is saved."""

import frappe
import frappe.client
from frappe.utils import safe_exec

from erpsmith import sanitizer
from erpsmith.registry import InvalidArguments, required_text, tool

SCRIPTS_DISABLED = ("Server scripts are disabled on this bench. Enable with `bench set-config -g server_script_enabled 1` "
	"(Frappe Cloud: private bench required).")
CLIENT_VIEWS = ("Form", "List")  # the Client Script `view` options on v15 and v16


class ServerScriptsDisabled(frappe.ValidationError):
	pass


def require_server_scripts():
	"""Spec §6: tools that save server-side Python say so up front when the bench cannot run it."""
	if not safe_exec.is_safe_exec_enabled():
		raise ServerScriptsDisabled(SCRIPTS_DISABLED)


def _client(row):
	return {"name": row["name"], "doctype": row["dt"], "scriptType": row["view"], "enabled": bool(row["enabled"])}


def _server(row):
	return {"name": row["name"], "scriptType": row["script_type"], "doctype": row["reference_doctype"],
		"event": row["doctype_event"], "apiMethod": row["api_method"], "frequency": row["event_frequency"],
		"enabled": not row["disabled"]}


@tool("create_client_script")
def create_client_script(doctype, script, name=None, scriptType="Form", enabled=True):
	if scriptType not in CLIENT_VIEWS:
		raise InvalidArguments(f"scriptType must be Form or List; Frappe has no {scriptType} client scripts")
	doc = frappe.get_doc({"doctype": "Client Script", "name": name or f"{required_text('doctype', doctype)} {scriptType} Script",
		"dt": doctype, "view": scriptType, "script": required_text("script", script), "enabled": int(bool(enabled))}).insert()
	return _client(doc.as_dict())


@tool("update_client_script")
def update_client_script(name, script=None, enabled=None):
	if script is None and enabled is None:
		raise InvalidArguments("pass script, enabled or both")
	doc = frappe.get_doc("Client Script", required_text("name", name))
	if script is not None:
		doc.script = script
	if enabled is not None:
		doc.enabled = int(bool(enabled))
	doc.save()
	return {"updated": doc.name, **_client(doc.as_dict())}


@tool("list_client_scripts")
def list_client_scripts(doctype=None):
	rows = frappe.get_list("Client Script", filters={"dt": doctype} if doctype else {}, limit_page_length=100,
		fields=["name", "dt", "view", "enabled", "modified"], order_by="modified desc")
	return {"count": len(rows), "scripts": [{**_client(r), "modified": r["modified"]} for r in rows]}


@tool("get_client_script")
def get_client_script(name):
	row = frappe.client.get("Client Script", required_text("name", name))
	return {**_client(row), "script": row["script"]}


@tool("create_server_script")
def create_server_script(name, scriptType, script, doctype=None, event=None, apiMethod=None, cronExpression=None, enabled=True):
	require_server_scripts()
	clean, removed = sanitizer.clean_or_raise(required_text("script", script), scriptType)
	data = {"doctype": "Server Script", "name": required_text("name", name), "script_type": required_text("scriptType", scriptType),
		"script": clean, "disabled": int(not enabled)}
	if scriptType == "DocType Event":
		data.update(reference_doctype=required_text("doctype", doctype), doctype_event=required_text("event", event))
	elif scriptType == "API":
		data.update(api_method=required_text("apiMethod", apiMethod), allow_guest=0)
	elif scriptType == "Scheduler Event":
		data["event_frequency"] = cronExpression or "Hourly"
	elif scriptType == "Permission Query":
		data["reference_doctype"] = required_text("doctype", doctype)
	doc = frappe.get_doc(data).insert()
	return {**_server(doc.as_dict()), "removedImports": removed}


@tool("update_server_script")
def update_server_script(name, script=None, enabled=None, event=None):
	if script is None and enabled is None and event is None:
		raise InvalidArguments("pass script, enabled, event or a combination")
	require_server_scripts()
	doc = frappe.get_doc("Server Script", required_text("name", name))
	removed = []
	if script is not None:
		doc.script, removed = sanitizer.clean_or_raise(script, doc.script_type)
	if enabled is not None:
		doc.disabled = int(not enabled)
	if event is not None:
		doc.doctype_event = event
	doc.save()
	return {"updated": doc.name, **_server(doc.as_dict()), "removedImports": removed}


@tool("list_server_scripts")
def list_server_scripts(doctype=None, scriptType=None):
	filters = {k: v for k, v in (("reference_doctype", doctype), ("script_type", scriptType)) if v}
	rows = frappe.get_list("Server Script", filters=filters, limit_page_length=100, order_by="modified desc",
		fields=["name", "script_type", "reference_doctype", "doctype_event", "api_method", "event_frequency", "disabled", "modified"])
	return {"count": len(rows), "scripts": [{**_server(r), "modified": r["modified"]} for r in rows]}


@tool("get_server_script")
def get_server_script(name):
	row = frappe.client.get("Server Script", required_text("name", name))
	return {**_server(row), "script": row["script"]}


@tool("apply_script_fix")
def apply_script_fix(scriptName, scriptType, fixedScript, enable=True):
	required_text("fixedScript", fixedScript)
	if scriptType == "client":
		doc = frappe.get_doc("Client Script", required_text("scriptName", scriptName))
		doc.script = fixedScript
		if enable:
			doc.enabled = 1
	elif scriptType == "server":
		require_server_scripts()
		doc = frappe.get_doc("Server Script", required_text("scriptName", scriptName))
		doc.script, _ = sanitizer.clean_or_raise(fixedScript, doc.script_type)
		if enable:
			doc.disabled = 0
	else:
		raise InvalidArguments('scriptType must be "client" or "server"')
	doc.save()
	return {"fixed": doc.name, "doctype": doc.doctype, "enabled": bool(doc.enabled) if scriptType == "client" else not doc.disabled}
