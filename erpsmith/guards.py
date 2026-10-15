"""Plan gate (RULE 10) and dangerous-op guard (RULE 16).

Ported from the Node server's planGate.js and handlers.checkDangerousOp. Plans are
stored per user in MCP Plan, so several clients and gunicorn workers share them,
and one user's approval never unlocks another user's call.
"""

import json

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from erpsmith import registry

# Every tool that is not read-only (registry.hints) is gated, except these: plans have to be made somehow,
# and memory tools write the site's notes, which spec §7 leaves outside the gate.
UNGATED = {"submit_plan", "approve_plan", "add_memory", "update_memory", "delete_memory"}
STATUS = {"Pending Approval": "pending_approval", "Approved": "approved"}
DEFAULT_TTL_MINUTES = 15
MAX_TTL_MINUTES = 1440


class GateBlocked(Exception):
	pass


class DangerousOp(Exception):
	pass


def canonical(args):
	return json.dumps(args or {}, sort_keys=True, separators=(",", ":"), default=str)


def is_mutating(tool, args):
	return tool not in UNGATED and not registry.is_read_only(tool)


def submit_plan(summary, changes):
	if not summary or not isinstance(changes, list) or not changes or not all(
		isinstance(c, dict) and isinstance(c.get("tool"), str) for c in changes
	):
		frappe.throw("submit_plan requires { summary: string, changes: [{tool, args, reason}] }")
	plan = frappe.get_doc({
		"doctype": "MCP Plan",
		"summary": summary,
		"changes": [{"tool": c["tool"], "args": canonical(c.get("args")), "reason": c.get("reason") or ""} for c in changes],
	}).insert(ignore_permissions=True)
	return {
		"planId": plan.name,
		"status": "pending_approval",
		"summary": summary,
		"changes": [{"idx": i, "tool": c.tool, "args": json.loads(c.args), "reason": c.reason} for i, c in enumerate(plan.changes)],
		"next_step": "This plan is shown to the user verbatim and waits for their explicit approval (for example "
		"'yes', 'go ahead' or 'approved'). approve_plan with this planId then unlocks the listed writes; until it "
		"succeeds, write tools are rejected.",
	}


def _own_plan(plan_id):
	"""The name of this user's plan `plan_id`. Anything else, including another user's plan, is unknown."""
	if not isinstance(plan_id, str):
		raise ValueError("planId must be a string")
	if frappe.db.get_value("MCP Plan", {"name": plan_id}, "owner") != frappe.session.user:
		frappe.throw(f"Unknown planId {plan_id}. Call submit_plan first.", frappe.DoesNotExistError)
	return plan_id


def approve_plan(planId, ttlMinutes=DEFAULT_TTL_MINUTES):
	if ttlMinutes is None:
		ttlMinutes = DEFAULT_TTL_MINUTES
	if isinstance(ttlMinutes, bool) or not isinstance(ttlMinutes, (int, float)) or not 1 <= ttlMinutes <= MAX_TTL_MINUTES:
		raise ValueError(f"ttlMinutes must be a number between 1 and {MAX_TTL_MINUTES}")  # NaN fails the range test too
	name = _own_plan(planId)
	approved_at = now_datetime()
	expires_at = add_to_date(approved_at, minutes=ttlMinutes)
	# Only the parent row: plan.save() would rewrite every change row from this process's copy, and a stale
	# consumed=0 would give back a step that a gated call in another worker has just used.
	frappe.db.set_value("MCP Plan", name, {"status": "Approved", "approved_at": approved_at, "expires_at": expires_at})
	return {
		"approved": True,
		"planId": name,
		"approvedAt": str(approved_at),
		"expiresAt": str(expires_at),
		"changesRemaining": frappe.db.count("MCP Plan Change", {"parent": name, "consumed": 0}),
	}


def _status(status, expires_at):
	if status == "Approved" and get_datetime(expires_at) <= now_datetime():
		return "expired"
	return STATUS[status]


def get_current_plan():
	name = frappe.db.get_value("MCP Plan", {"owner": frappe.session.user}, "name", order_by="creation desc")
	if not name:
		return {"plan": None}
	p = frappe.get_doc("MCP Plan", name)
	return {"plan": {
		"planId": p.name,
		"status": _status(p.status, p.expires_at),
		"summary": p.summary,
		"createdAt": str(p.creation),
		"approvedAt": p.approved_at and str(p.approved_at),
		"expiresAt": p.expires_at and str(p.expires_at),
		"changes": [
			{"idx": i, "tool": c.tool, "args": json.loads(c.args), "reason": c.reason,
				"consumed": bool(c.consumed), "consumedAt": c.consumed_at and str(c.consumed_at)}
			for i, c in enumerate(p.changes)
		],
	}}


def _unused_changes(plan_names):
	return frappe.get_all("MCP Plan Change", filters={"parent": ("in", plan_names), "consumed": 0},
		fields=["name", "parent", "idx", "tool", "args"], order_by="creation asc, idx asc")


def _consume(change_name):
	"""Mark one change used. False if a concurrent request used it first: the UPDATE only matches
	consumed=0 and waits on the other request's row lock, so a stale read cannot consume twice."""
	frappe.db.sql("update `tabMCP Plan Change` set consumed=1, consumed_at=%s where name=%s and consumed=0",
		(now_datetime(), change_name))
	return frappe.db._cursor.rowcount == 1


def check_gate(tool, args):
	"""Raise GateBlocked unless an approved, unexpired plan of this user has an unused change
	with exactly this tool and args; consume that change."""
	if not is_mutating(tool, args):
		return
	plans = frappe.get_all("MCP Plan", filters={"owner": frappe.session.user},
		fields=["name", "status", "expires_at"], order_by="creation desc", limit=50)
	if not plans:
		raise GateBlocked(
			f"BLOCKED by plan gate: '{tool}' is a mutating operation. No plan has been submitted. "
			"Required workflow: (1) call submit_plan with { summary, changes: [{tool, args, reason}, ...] }, "
			"(2) show the returned plan to the user verbatim, (3) wait for explicit user approval, "
			"(4) call approve_plan with the planId, (5) then call mutating tools. "
			"Args in each call must EXACTLY match the approved plan entry."
		)
	live = [p.name for p in plans if _status(p.status, p.expires_at) == "approved"]
	if not live:
		last = plans[0]
		if last.status != "Approved":
			raise GateBlocked(
				f"BLOCKED by plan gate: '{tool}' called but plan {last.name} is 'pending_approval'. "
				f"Show the plan to the user, get explicit approval, then call approve_plan with planId='{last.name}'."
			)
		raise GateBlocked(f"BLOCKED by plan gate: approved plan {last.name} expired at {last.expires_at}. "
			"Submit a new plan with submit_plan.")
	want = canonical(args)
	unused = _unused_changes(live)
	for row in unused:
		if row.tool == tool and row.args == want and _consume(row.name):
			return
	# Changes that match this call are gone: used by this plan step or taken by a concurrent request.
	remaining = [{"planId": r.parent, "idx": r.idx - 1, "tool": r.tool, "args": json.loads(r.args)}
		for r in unused if not (r.tool == tool and r.args == want)]
	raise GateBlocked(
		f"BLOCKED by plan gate: '{tool}' call does not match any remaining approved change. "
		"Args must match EXACTLY (key order does not matter). "
		f"Remaining approved changes: {json.dumps(remaining)}. "
		"If you must deviate, call submit_plan again with the corrected changes and get re-approval."
	)


BULK_DELETE_LIMIT = 10


def _json_value(value, default=None):
	"""`value` parsed as JSON when it is a string, else `value` itself; `default` when the string is not JSON."""
	if not isinstance(value, str):
		return value
	try:
		return json.loads(value)
	except ValueError:
		return default


PROTECTED_DOCTYPES = {"User", "Role", "DocType", "Module Def", "System Settings", "Accounts Settings", "Stock Settings", "Company"}
USER_SECURITY_FIELDS = ("roles", "password", "new_password", "api_key", "api_secret", "user_type", "enabled")
CHILD_TABLE_FIELDS = {"roles", "permissions", "items", "taxes"}


def check_dangerous_op(tool, args):
	"""Operations blocked even with an approved plan (RULE 16)."""
	data = args.get("data")
	if tool in ("create_record", "update_record") and args.get("doctype") == "User" and isinstance(data, dict):
		attempting = [f for f in USER_SECURITY_FIELDS if f in data]
		if attempting:
			raise DangerousOp(
				f"DANGEROUS OP BLOCKED — Never modify User.{', '.join(attempting)} through the API.\n"
				"Reason: set_value on child tables (roles) REPLACES all rows, wiping every role from the user.\n"
				"Safe alternatives:\n"
				"  • User roles → Desk: User > [user] > Roles\n"
				"  • Password reset → Desk: User > [user] > Reset Password\n"
				"  • API keys → Desk: User > [user] > API Access\n"
				"See RULE 16."
			)
	if tool == "delete_record" and isinstance(args.get("doctype"), str) and args["doctype"] in PROTECTED_DOCTYPES:
		raise DangerousOp(
			f"DANGEROUS OP BLOCKED — Deleting from '{args['doctype']}' can break ERPNext entirely.\n"
			"If you genuinely need this, do it in Desk with proper confirmation. See RULE 16."
		)
	if tool != "call_method":
		return
	method, margs = args.get("method"), args.get("args")
	margs = margs if isinstance(margs, dict) else {}
	bulk = None
	if method == "frappe.client.delete" and isinstance(margs.get("names"), list):
		bulk = margs["names"]
	elif method == "frappe.desk.reportview.delete_items":  # the list view's bulk delete; items is a JSON list
		bulk = _json_value(margs.get("items"))
	if isinstance(bulk, list) and len(bulk) > BULK_DELETE_LIMIT:
		raise DangerousOp(
			f"DANGEROUS OP BLOCKED — Bulk delete of {len(bulk)} records in one call.\n"
			"Max 10 per call. Split into batches and get approval for each batch. See RULE 16."
		)
	if method == "frappe.client.set_value":
		fieldname = margs.get("fieldname")
		if isinstance(fieldname, str) and not margs.get("value"):
			fieldname = _json_value(fieldname, fieldname)  # set_value reads a JSON object string as several fields
		if isinstance(fieldname, (dict, list)) or (isinstance(fieldname, str) and fieldname in CHILD_TABLE_FIELDS):
			raise DangerousOp(
				f"DANGEROUS OP BLOCKED — frappe.client.set_value with fieldname='{fieldname}' replaces the entire child table.\n"
				"Use update_record instead, which sends the full document. See RULE 16."
			)
