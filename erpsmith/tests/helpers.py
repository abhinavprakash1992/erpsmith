import json
from contextlib import contextmanager
from unittest.mock import patch

import frappe

from erpsmith import guards, mcp, memory, vector_cache, vectors


def call(name, args=None):
	"""Run a tool the way the endpoint does. Returns (is_error, parsed result)."""
	result = mcp.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
		"params": {"name": name, "arguments": args or {}}})["result"]
	return result.get("isError", False), json.loads(result["content"][0]["text"])


def approved(tool, args):
	"""Submit and approve a one-step plan for `tool` with exactly `args`."""
	guards.approve_plan(guards.submit_plan("test", [{"tool": tool, "args": args}])["planId"])


def run(tool, args=None):
	"""call() a write tool under its own approved one-step plan. Returns (is_error, parsed result)."""
	approved(tool, args or {})
	return call(tool, args)


def make_user(email):
	"""A user with no roles. Returns the email."""
	if not frappe.db.exists("User", email):
		frappe.get_doc({"doctype": "User", "email": email, "first_name": "MCP Test",
			"send_welcome_email": 0}).insert(ignore_permissions=True)
	return email


def empty_memory_table():
	"""Tests rank and count notes, so they start from an empty table. Rolled back with the test class."""
	frappe.db.delete("MCP Memory")
	vector_cache.invalidate("MCP Memory")  # a raw delete skips the controller; also bumps again on rollback


def forget_seeds():
	"""A site that never received a seed: no notes and no applied keys. Rolled back with the test class."""
	empty_memory_table()
	frappe.db.delete("DefaultValue", {"parent": "__default", "defkey": memory.APPLIED_DEFAULT})


def empty_knowledge_table():
	frappe.db.delete("MCP Knowledge Chunk")
	vector_cache.invalidate("MCP Knowledge Chunk")


def knowledge_chunk(key, kind=None, title=None, summary="a summary", content="some content", source="/x"):
	"""A chunk dict as knowledge.sync takes it; the kind defaults to the part of the key before '::'."""
	return {"chunk_key": key, "kind": kind or key.split("::", 1)[0], "title": title or key,
		"source": source, "summary": summary, "content": content}


@contextmanager
def counted_sessions():
	"""Run a block against a cold embedding model and count the ONNX sessions it builds.

	On entry `vectors._model` is saved and set to None, so the first embed has to build a session;
	`onnxruntime.InferenceSession` is wrapped to count constructions (the real class still runs).
	Yields a list with one item per construction: `len(built)` is how many sessions the block loaded.
	On exit `vectors._model` goes back to what it was, so other tests keep their warm model.
	"""
	import onnxruntime

	warm, real, built = vectors._model, onnxruntime.InferenceSession, []

	def counting(*args, **kwargs):
		built.append(1)
		return real(*args, **kwargs)

	vectors._model = None
	try:
		with patch.object(onnxruntime, "InferenceSession", counting):
			yield built
	finally:
		vectors._model = warm


TEST_DOCTYPE_PREFIX = "ERPSmith Test"


def drop_doctype(name):
	"""Remove a DocType a test made, with its table. DocType changes commit, so the test's rollback cannot remove it."""
	assert name.startswith(TEST_DOCTYPE_PREFIX), f"drop_doctype only removes test DocTypes, not {name}"
	frappe.set_user("Administrator")
	if frappe.db.exists("DocType", name):
		frappe.delete_doc("DocType", name, force=True)
	for doctype, field in (("Custom Field", "dt"), ("Property Setter", "doc_type"), ("Custom DocPerm", "parent")):
		frappe.db.delete(doctype, {field: name})
	frappe.db.sql_ddl(f"drop table if exists `tab{name}`")  # commits
	frappe.db.commit()


def forget_plans(user="Administrator"):
	"""Delete `user`'s plans. A schema change commits the plan it ran under, which no class rollback removes."""
	for name in frappe.get_all("MCP Plan", filters={"owner": user}, pluck="name"):
		frappe.delete_doc("MCP Plan", name, ignore_permissions=True, force=True)
	frappe.db.commit()
