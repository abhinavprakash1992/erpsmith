"""MCP endpoint: stateless Streamable HTTP, one JSON-RPC message per POST.

    POST https://<site>/api/method/erpsmith.mcp.handle

Tools run in-process as the OAuth-authenticated user. Each tool call runs in a
savepoint, so a failing tool leaves nothing behind.
"""

import json
from pathlib import Path

import frappe
from werkzeug.wrappers import Response

import erpsmith
from erpsmith import guards

SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
_HERE = Path(__file__).parent
SCHEMAS = {t["name"]: t for t in json.loads((_HERE / "tools.json").read_text())}
INSTRUCTIONS = (_HERE / "instructions.md").read_text()
IMPLS = {}

READ_ONLY_PREFIXES = ("get_", "list_", "query_", "search_", "debug_")
READ_ONLY_EXACT = {"full_health_check", "dump_memory", "call_method_read"}
DESTRUCTIVE_PREFIXES = ("delete_", "remove_", "update_", "set_", "fix_", "apply_", "rename_", "reorder_", "activate_")
DESTRUCTIVE_EXACT = {"call_method"}


def tool(name):
	"""Register the implementation of tool `name`; its schema lives in tools.json."""

	def register(fn):
		if name not in SCHEMAS:
			raise KeyError(f"{name} has no schema in tools.json")
		if name in IMPLS:
			raise ValueError(f"{name} already has an implementation")
		IMPLS[name] = fn
		return fn

	return register


def annotations(name):
	if name in READ_ONLY_EXACT or name.startswith(READ_ONLY_PREFIXES):
		hints = {"readOnlyHint": True}
	else:
		destructive = name in DESTRUCTIVE_EXACT or name.startswith(DESTRUCTIVE_PREFIXES)
		hints = {"readOnlyHint": False, "destructiveHint": destructive}
	return {"title": SCHEMAS[name]["title"], **hints}


def list_tools():
	return [
		{
			"name": name,
			"title": s["title"],
			"description": s["description"],
			"inputSchema": s["inputSchema"],
			"annotations": annotations(name),
		}
		for name, s in SCHEMAS.items()
	]


def suggestion(e):
	msg = str(e)
	if msg.startswith("BLOCKED by plan gate"):
		return ("Call submit_plan first, show the plan to the user verbatim, wait for explicit approval, "
			"then call approve_plan. See RULE 10.")
	if msg.startswith("DANGEROUS OP BLOCKED"):
		return "This operation is permanently blocked. The error names the safe alternative; do not work around it. See RULE 16."
	if msg.startswith("Unknown tool"):
		return "Call tools/list to see the available tools."
	if isinstance(e, frappe.PermissionError):
		return ("The signed-in ERPNext user lacks this permission. Tools run with that user's roles; "
			"ask an administrator to grant the role.")
	if isinstance(e, frappe.DoesNotExistError):
		return "Not found. Check the DocType and name with get_doctype or query_records."
	if isinstance(e, frappe.DuplicateEntryError):
		return "Already exists. Use update instead of create, or check with get_record first."
	if isinstance(e, frappe.LinkValidationError):
		return "A linked record does not exist. Verify it with query_records."
	if isinstance(e, frappe.ValidationError):
		return "Validation failed. Check required fields and allowed values with get_doctype."
	if "__import__" in msg or "ImportError" in msg:
		return "The script sandbox blocks all imports. Remove every import; frappe, json and datetime are already available. See RULE 1."
	if "not whitelisted" in msg:
		return "That method is not whitelisted, so it cannot be called. Check the dotted path."
	if isinstance(e, TypeError):
		return "Wrong arguments for this tool. Check the tool's input schema in tools/list."
	return "Try call_method with the equivalent frappe.client.* method."


def _error_result(e):
	body = {"error": True, "message": str(e) or type(e).__name__, "type": type(e).__name__, "suggestion": suggestion(e)}
	return {"content": [{"type": "text", "text": json.dumps(body, indent=2)}], "isError": True}


def _undo_tool_writes():
	try:
		frappe.db.rollback(save_point="mcp_tool")
	except Exception:
		# The savepoint is gone (the tool committed, ran DDL, or the transaction was rolled back
		# by a deadlock): undo the whole transaction instead.
		frappe.db.rollback()
	# A savepoint rollback leaves before/after_commit callbacks queued, and they would fire when the
	# request commits (webhooks, realtime events, enqueue_after_commit jobs) for rows that no longer exist.
	frappe.db.before_commit.reset()
	frappe.db.after_commit.reset()


def call_tool(name, args):
	impl = IMPLS.get(name)
	if impl is None:
		return _error_result(LookupError(f"Unknown tool: {name}"))
	frappe.db.savepoint("mcp_tool")
	try:
		guards.check_gate(name, args)
		guards.check_dangerous_op(name, args)
		result = impl(**args)
		text = json.dumps(result, indent=2, default=str)
	except Exception as e:
		_undo_tool_writes()
		return _error_result(e)
	return {"content": [{"type": "text", "text": text}]}


def _rpc_error(mid, code, message):
	return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def dispatch(msg):
	"""Handle one JSON-RPC message. Returns the response, or None for a notification."""
	if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
		return _rpc_error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
	if "id" not in msg:
		return None
	mid, method, params = msg["id"], msg["method"], msg.get("params", {})
	if not isinstance(params, dict):
		return _rpc_error(mid, -32602, "Invalid params: expected an object")
	if method == "initialize":
		asked = params.get("protocolVersion")
		result = {
			"protocolVersion": asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0],
			"capabilities": {"tools": {"listChanged": False}},
			"serverInfo": {"name": "erpsmith", "title": "ERPSmith", "version": erpsmith.__version__},
			"instructions": INSTRUCTIONS,
		}
	elif method == "ping":
		result = {}
	elif method == "tools/list":
		result = {"tools": list_tools()}
	elif method == "tools/call":
		arguments = params.get("arguments") or {}
		if not isinstance(arguments, dict):
			return _rpc_error(mid, -32602, "Invalid params: arguments must be an object")
		if not isinstance(params.get("name"), str):
			return _rpc_error(mid, -32602, "Invalid params: name must be a string")
		result = call_tool(params["name"], arguments)
	else:
		return _rpc_error(mid, -32601, f"Method not found: {method}")
	return {"jsonrpc": "2.0", "id": mid, "result": result}


def _json(body, status=200):
	return Response(json.dumps(body, default=str), status=status, mimetype="application/json")


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def handle():
	if frappe.session.user == "Guest":
		metadata = frappe.utils.get_url("/.well-known/oauth-protected-resource")
		return Response(status=401, headers={"WWW-Authenticate": f'Bearer resource_metadata="{metadata}"'})
	if frappe.request.method != "POST":
		return Response(status=405, headers={"Allow": "POST"})
	try:
		msg = json.loads(frappe.request.get_data(as_text=True))
	except ValueError:
		return _json(_rpc_error(None, -32700, "Parse error"))
	reply = dispatch(msg)
	return Response(status=202) if reply is None else _json(reply)


# Tool modules register themselves with @tool on import.
from erpsmith.tools import plan  # noqa: E402, F401

_missing = set(SCHEMAS) - set(IMPLS)
if _missing:
	raise RuntimeError(f"tools.json entries without an implementation: {sorted(_missing)}")
