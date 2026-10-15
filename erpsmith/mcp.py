"""MCP endpoint: stateless Streamable HTTP, one JSON-RPC message per POST.

    POST https://<site>/api/method/erpsmith.mcp.handle

Tools run in-process as the OAuth-authenticated user. Each tool call runs in a
savepoint, so a failing tool leaves nothing behind.
"""

import importlib
import json
from pathlib import Path

import frappe
from werkzeug.wrappers import Response

import erpsmith
from erpsmith import guards
from erpsmith.oauth import PROTECTED_RESOURCE, site_url
from erpsmith import registry
from erpsmith.registry import IMPLS, SCHEMAS, InvalidArguments, tool  # noqa: F401  re-exported: tests patch mcp.SCHEMAS and mcp.IMPLS

MCP_PATH = "/api/method/erpsmith.mcp.handle"
SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
INSTRUCTIONS = (Path(__file__).parent / "instructions.md").read_text(encoding="utf-8")

# Every module that registers tools. Importing one registers its tools; a new tool module goes in this list.
TOOL_MODULES = ("erpsmith.tools.plan", "erpsmith.tools.records", "erpsmith.memory", "erpsmith.knowledge", "erpsmith.tools.doctype",
	"erpsmith.tools.scripts", "erpsmith.tools.builders", "erpsmith.tools.debug",
	"erpsmith.tools.fixes")
for _module in TOOL_MODULES:
	importlib.import_module(_module)

# Every schema needs an implementation. The reverse cannot happen: tool() refuses a name that has no schema.
_missing = set(SCHEMAS) - set(IMPLS)
if _missing:
	raise RuntimeError(f"tools.json entries without an implementation: {sorted(_missing)}")
for _name in SCHEMAS:
	registry.hints(_name)  # a tool whose name matches no annotation rule fails here, at startup


def annotations(name):
	return {"title": SCHEMAS[name]["title"], **registry.hints(name)}


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
	"""What the client can do about error `e`, in descriptive voice (spec §9: behavioural guidance lives in instructions.md)."""
	msg = str(e)
	if msg.startswith("BLOCKED by plan gate"):
		return ("Write tools run only after the user approves a plan: submit_plan, the plan shown to the user verbatim, "
			"their explicit approval, then approve_plan. See RULE 10.")
	if msg.startswith("DANGEROUS OP BLOCKED"):
		return "This operation is blocked even with an approved plan; the error names the safe alternative. See RULE 16."
	if msg.startswith("Unknown tool"):
		return "tools/list shows the available tools."
	if msg.startswith("Server scripts are disabled"):
		return "Only a bench administrator can enable server scripts; the message gives the command."
	if "is a standard DocType" in msg:
		return "The error names the upgrade-safe tools that change a standard DocType."
	if "not whitelisted" in msg:
		return "Only whitelisted methods can be called, and the dotted path may be wrong."
	if msg.startswith("The script would fail in Frappe's sandbox"):
		return "Each listed line names a replacement that runs in the sandbox. See RULE 1."
	if isinstance(e, InvalidArguments):
		return "An argument is missing or out of range; the tool's input schema in tools/list shows what it takes."
	if isinstance(e, frappe.PermissionError):
		return ("The signed-in ERPNext user lacks this permission. Tools run with that user's roles, "
			"so an administrator has to grant the role.")
	if isinstance(e, frappe.DoesNotExistError) and msg.startswith("No memory with id"):
		return "No note has that id. The ids of existing notes are shown by list_memory and search_memory."
	if isinstance(e, frappe.DoesNotExistError):
		return "Not found. get_doctype and query_records show what exists."
	if isinstance(e, frappe.DuplicateEntryError):
		return "A record with that name already exists; get_record shows it and update_record changes it."
	if isinstance(e, frappe.LinkValidationError):
		return "A linked record does not exist; query_records shows the records that do."
	if isinstance(e, frappe.ValidationError):
		return "Frappe rejected the values; get_doctype shows the required fields and allowed values."
	if "__import__" in msg or "ImportError" in msg:
		return ("The script sandbox rejects every import; frappe and json are already available, "
			"and date helpers live in frappe.utils. See RULE 1.")
	if isinstance(e, TypeError):
		return "The arguments do not match this tool; its input schema in tools/list shows what it takes."
	return "This is Frappe's own error message. get_error_logs lists recent server errors, and search_memory may hold a past fix."


PARTIAL = ("Some of this tool's changes may already be saved: it changed the database schema, which commits "
	"immediately, so they could not be rolled back. get_doctype and list_fields show the current state.")


def _error_result(e, partial=False):
	body = {"error": True, "message": str(e) or type(e).__name__, "type": type(e).__name__, "suggestion": suggestion(e)}
	if partial:
		body["partial"] = PARTIAL
	return {"content": [{"type": "text", "text": json.dumps(body, indent=2)}], "isError": True}


def undo_tool_writes(save_point="mcp_tool"):
	"""Roll back to `save_point`. Returns False when the savepoint was gone and the whole transaction was rolled
	back instead: whatever the tool committed itself (a schema change commits at once) is then kept."""
	try:
		frappe.db.rollback(save_point=save_point)
	except Exception:
		# The savepoint is gone (the tool committed, ran DDL, or the transaction was rolled back
		# by a deadlock): undo the whole transaction instead. That runs the rollback callbacks itself.
		frappe.db.rollback()
		kept = False
	else:
		# A savepoint rollback skips them. They undo what the database cannot: File blobs on disk,
		# cached docs and metadata. The tool's own error is what the client needs, so a failing callback is logged.
		try:
			frappe.db.after_rollback.run()
		except Exception:
			frappe.logger().warning("erpsmith: an after_rollback callback failed", exc_info=True)
		kept = True
	# A savepoint rollback leaves before/after_commit callbacks queued, and they would fire when the
	# request commits (webhooks, realtime events, enqueue_after_commit jobs) for rows that no longer exist.
	frappe.db.before_commit.reset()
	frappe.db.after_commit.reset()
	return kept


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
		return _error_result(e, partial=not undo_tool_writes())
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


def www_authenticate():
	"""The 401 challenge (RFC 9728): where a client learns how to get a token."""
	return f'Bearer resource_metadata="{site_url()}/{PROTECTED_RESOURCE}"'


def add_www_authenticate(response):
	"""after_request hook. Frappe rejects a bad, expired or revoked bearer token before handle() runs, so on v15
	that 401 has no challenge and a client cannot tell it should refresh its token. v16 core adds one itself
	(after this hook, replacing ours), so an existing header is left alone."""
	try:
		if (response is not None and response.status_code == 401 and frappe.request.path == MCP_PATH
				and "WWW-Authenticate" not in response.headers):
			response.headers["WWW-Authenticate"] = www_authenticate()
	except Exception:
		frappe.logger().warning("erpsmith: could not add WWW-Authenticate to a 401", exc_info=True)


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def handle():
	if frappe.session.user == "Guest":
		return Response(status=401, headers={"WWW-Authenticate": www_authenticate()})
	if frappe.request.method != "POST":
		return Response(status=405, headers={"Allow": "POST"})
	try:
		msg = json.loads(frappe.request.get_data(as_text=True))
	except ValueError:
		return _json(_rpc_error(None, -32700, "Parse error"))
	reply = dispatch(msg)
	return Response(status=202) if reply is None else _json(reply)
