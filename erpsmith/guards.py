"""Plan gate (RULE 10) and dangerous-op guard (RULE 16).

Ported from the Node server's planGate.js and handlers.checkDangerousOp. Plans are
stored per user in MCP Plan, so several clients and gunicorn workers share them,
and one user's approval never unlocks another user's call.
"""

import json

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

MUTATING_PREFIXES = ("create_", "update_", "delete_", "add_", "remove_", "set_", "apply_", "fix_", "activate_", "rename_", "reorder_")
UNGATED = {"submit_plan", "approve_plan", "get_current_plan", "add_memory", "update_memory", "delete_memory"}
STATUS = {"Pending Approval": "pending_approval", "Approved": "approved"}


class GateBlocked(Exception):
	pass


class DangerousOp(Exception):
	pass


def canonical(args):
	return json.dumps(args or {}, sort_keys=True, separators=(",", ":"), default=str)


def is_mutating(tool, args):
	if tool in UNGATED:
		return False
	return tool == "call_method" or tool.startswith(MUTATING_PREFIXES)


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
		"next_step": "Show this plan to the user verbatim. Wait for explicit approval (e.g. 'yes', 'go ahead', "
		"'approved'). Then call approve_plan with this planId. Do NOT call any mutating tool before approve_plan succeeds.",
	}


def _own_plan(plan_id):
	if not frappe.db.exists("MCP Plan", {"name": plan_id, "owner": frappe.session.user}):
		frappe.throw(f"Unknown planId {plan_id}. Call submit_plan first.", frappe.DoesNotExistError)
	return frappe.get_doc("MCP Plan", plan_id)


def approve_plan(planId, ttlMinutes=15):
	plan = _own_plan(planId)
	plan.status = "Approved"
	plan.approved_at = now_datetime()
	plan.expires_at = add_to_date(plan.approved_at, minutes=ttlMinutes)
	plan.save(ignore_permissions=True)
	return {
		"approved": True,
		"planId": plan.name,
		"approvedAt": str(plan.approved_at),
		"expiresAt": str(plan.expires_at),
		"changesRemaining": sum(1 for c in plan.changes if not c.consumed),
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
	unused = frappe.get_all("MCP Plan Change", filters={"parent": ("in", live), "consumed": 0},
		fields=["name", "parent", "idx", "tool", "args"], order_by="creation asc, idx asc")
	for row in unused:
		if row.tool == tool and row.args == want:
			frappe.db.set_value("MCP Plan Change", row.name, {"consumed": 1, "consumed_at": now_datetime()},
				update_modified=False)
			return
	remaining = [{"planId": r.parent, "idx": r.idx - 1, "tool": r.tool, "args": json.loads(r.args)} for r in unused]
	raise GateBlocked(
		f"BLOCKED by plan gate: '{tool}' call does not match any remaining approved change. "
		"Args must match EXACTLY (key order does not matter). "
		f"Remaining approved changes: {json.dumps(remaining)}. "
		"If you must deviate, call submit_plan again with the corrected changes and get re-approval."
	)


PROTECTED_DOCTYPES = {"User", "Role", "DocType", "Module Def", "System Settings", "Accounts Settings", "Stock Settings", "Company"}
USER_SECURITY_FIELDS = ("roles", "password", "new_password", "api_key", "api_secret", "user_type", "enabled")
CHILD_TABLE_FIELDS = {"roles", "permissions", "items", "taxes"}


def check_dangerous_op(tool, args):
	"""Operations blocked even with an approved plan (RULE 16)."""
	if tool in ("create_record", "update_record") and args.get("doctype") == "User":
		attempting = [f for f in USER_SECURITY_FIELDS if f in (args.get("data") or {})]
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
	if tool == "delete_record" and args.get("doctype") in PROTECTED_DOCTYPES:
		raise DangerousOp(
			f"DANGEROUS OP BLOCKED — Deleting from '{args['doctype']}' can break ERPNext entirely.\n"
			"If you genuinely need this, do it in Desk with proper confirmation. See RULE 16."
		)
	method, margs = args.get("method"), args.get("args") or {}
	if tool == "call_method" and method == "frappe.client.delete" and isinstance(margs.get("names"), list) and len(margs["names"]) > 10:
		raise DangerousOp(
			f"DANGEROUS OP BLOCKED — Bulk delete of {len(margs['names'])} records in one call.\n"
			"Max 10 per call. Split into batches and get approval for each batch. See RULE 16."
		)
	if tool == "call_method" and method == "frappe.client.set_value":
		fieldname = margs.get("fieldname")
		if isinstance(fieldname, dict) or fieldname in CHILD_TABLE_FIELDS:
			raise DangerousOp(
				f"DANGEROUS OP BLOCKED — frappe.client.set_value with fieldname='{fieldname}' replaces the entire child table.\n"
				"Use update_record instead, which sends the full document. See RULE 16."
			)
