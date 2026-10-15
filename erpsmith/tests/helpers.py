import json

import frappe

from erpsmith import mcp


def call(name, args=None):
	"""Run a tool the way the endpoint does. Returns (is_error, parsed result)."""
	result = mcp.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
		"params": {"name": name, "arguments": args or {}}})["result"]
	return result.get("isError", False), json.loads(result["content"][0]["text"])


def make_user(email):
	"""A user with no roles. Returns the email."""
	if not frappe.db.exists("User", email):
		frappe.get_doc({"doctype": "User", "email": email, "first_name": "MCP Test",
			"send_welcome_email": 0}).insert(ignore_permissions=True)
	return email


def empty_memory_table():
	"""Tests rank and count notes, so they start from an empty table. Rolled back with the test class."""
	frappe.db.delete("MCP Memory")
