"""call_method and call_method_read: the whitelisted Python methods /api/method/<dotted.path> serves, called in-process.

call_method_read is read-only by construction for Frappe's own commit paths: it runs in a savepoint that is always
rolled back, and a commit inside the method (frappe.db.commit, or a schema change through frappe.db.sql_ddl) is refused.
"""

import frappe
from frappe.model.document import Document

from erpsmith.registry import InvalidArguments, required_text, tool

READ_SAVEPOINT = "mcp_read"


def _resolve(method):
	"""The function /api/method/<method> runs, after the same overrides and whitelist check as the REST handler."""
	method = required_text("method", method)
	overrides = frappe.get_hooks("override_whitelisted_methods", {}).get(method)
	if overrides:
		method = overrides[-1]
	try:
		fn = frappe.get_attr(method)
	except (AttributeError, ImportError, frappe.AppNotInstalledError):
		raise InvalidArguments(f"No method {method}: the dotted path of a whitelisted function is needed, "
			"e.g. frappe.client.get_count") from None
	if fn not in frappe.whitelisted:
		raise frappe.PermissionError(f"{method} is not whitelisted, so it cannot be called")
	frappe.is_whitelisted(fn)  # the REST handler's check: guest access and input sanitising
	return fn


def _call(method, args):
	if args is not None and not isinstance(args, dict):
		raise InvalidArguments("args must be an object of keyword arguments")
	fn = _resolve(method)
	saved = frappe.local.form_dict
	frappe.local.form_dict = frappe._dict(args or {})  # some methods read frappe.form_dict instead of their arguments
	try:
		result = frappe.call(fn, **(args or {}))
	finally:
		frappe.local.form_dict = saved
	# not hasattr(result, "as_dict"): on a frappe._dict every missing attribute reads as None, so hasattr is always true
	return result.as_dict() if isinstance(result, Document) else result


@tool("call_method")
def call_method(method, args=None):
	return {"method": method, "result": _call(method, args)}


def _refuse_commit():
	frappe.db.before_commit.add(_refuse_commit)  # running the callbacks removes them; stay queued for a second attempt
	raise frappe.ValidationError("call_method_read rolls back everything the method writes, so a method that commits "
		"cannot run there; call_method runs it and keeps its writes.")


@tool("call_method_read")
def call_method_read(method, args=None):
	from erpsmith import mcp  # here, not at the top: erpsmith.mcp imports this module while it loads

	frappe.db.savepoint(READ_SAVEPOINT)
	frappe.db.before_commit.add(_refuse_commit)
	try:
		result = _call(method, args)
	finally:
		mcp.undo_tool_writes(READ_SAVEPOINT)  # also drops _refuse_commit and whatever the method queued for a commit
	return {"method": method, "result": result, "rolledBack": True}
