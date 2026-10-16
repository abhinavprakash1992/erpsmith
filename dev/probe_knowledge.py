#!/usr/bin/env python3
"""Retrieval cases, memory footprint and search latency on a dev site, then the site-knowledge cases of Plan 3b
(workflow, server scripts and profile notes), which run against fixtures this script creates and removes.

    cd ~/frappe-bench/sites && ../env/bin/python /workspace/erpsmith/dev/probe_knowledge.py test15.localhost
"""

import resource
import sys
import time
from contextlib import contextmanager

import frappe

GADGET = "ERPSmith Test Gadget"
ROLE = "ERPSmith Test Approver"
WORKFLOW = "ERPSmith Test Gadget Approval"
SCRIPTS = ("ERPSmith Test Gadget Archive On Submit", "ERPSmith Test Gadget Notify On Submit")

CASES = [  # from the Node server's knowledge.test.js; the manual kind was called "doc" there
	("python script that runs on document save", "doctype", "Server Script"),
	("how to configure perpetual inventory accounts", "manual", "Perpetual Inventory"),
	("create a delivery note from a sales order", "manual", "Delivery Note"),
]


def rss_mb():
	peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
	return peak // (1024 * 1024) if sys.platform == "darwin" else peak // 1024


def remove_fixtures():
	from erpsmith.tests.helpers import drop_doctype

	frappe.set_user("Administrator")
	for name in SCRIPTS:
		if frappe.db.exists("Server Script", name):
			frappe.delete_doc("Server Script", name, force=True)
	if frappe.db.exists("Workflow", WORKFLOW):
		frappe.delete_doc("Workflow", WORKFLOW, force=True)
	drop_doctype(GADGET)  # commits
	if frappe.db.exists("Role", ROLE):
		frappe.delete_doc("Role", ROLE, force=True)
	frappe.db.commit()


def make_fixtures():
	from erpsmith.tests.helpers import server_scripts

	remove_fixtures()  # a run that died leaves them behind
	frappe.get_doc({"doctype": "Role", "role_name": ROLE}).insert()
	frappe.get_doc({"doctype": "DocType", "name": GADGET, "module": "Custom", "custom": 1, "autoname": "hash", "is_submittable": 1,
		"fields": [{"fieldname": "title", "label": "Title", "fieldtype": "Data"},
			{"fieldname": "workflow_state", "label": "Status", "fieldtype": "Link", "options": "Workflow State"}],
		"permissions": [{"role": "System Manager", "read": 1, "write": 1, "create": 1, "submit": 1}]}).insert()
	frappe.get_doc({"doctype": "Workflow", "workflow_name": WORKFLOW, "document_type": GADGET, "workflow_state_field": "workflow_state",
		"is_active": 1, "states": [{"state": "Pending", "doc_status": "0", "allow_edit": ROLE},
			{"state": "Approved", "doc_status": "0", "allow_edit": ROLE}],
		"transitions": [{"state": "Pending", "action": "Approve", "next_state": "Approved", "allowed": ROLE}]}).insert()
	with server_scripts():
		for name, line in zip(SCRIPTS, ('frappe.log("gadget archived")', 'frappe.msgprint("gadget submitted")')):
			frappe.get_doc({"doctype": "Server Script", "name": name, "script_type": "DocType Event", "reference_doctype": GADGET,
				"doctype_event": "After Submit", "script": line}).insert()
	frappe.db.commit()


@contextmanager
def fixtures():
	try:
		make_fixtures()
		yield
	finally:
		remove_fixtures()


def timed_build(knowledge):
	started = time.time()
	counts = knowledge.build(include_manual=False)
	return time.time() - started, counts


def site_cases(knowledge, memory):
	"""Plan 3b: three questions about this site's own workflow, scripts and apps."""
	with fixtures():
		seconds, counts = timed_build(knowledge)
		print(f"BUILD with customizations: {seconds:.1f} s, {counts}")

		def titles(query, **kw):
			return [r["title"] for r in knowledge.search_erpnext_knowledge(query, topK=5, **kw)["results"]]

		def notes(query):
			return [r["id"] for r in memory.search_memory(query, topK=5, topic="context")["results"]]

		kn, mem = titles("who approves gadgets"), notes("who approves gadgets")
		ok = WORKFLOW in kn[:3] or "profile__workflows" in mem[:3]
		print("HIT " if ok else "MISS", "workflow or workflows note in top 3 <- who approves gadgets", kn[:3], mem[:3])
		kn = titles("what runs when a gadget is submitted")
		ok = all(name in kn[:5] for name in SCRIPTS)
		print("HIT " if ok else "MISS", "both After Submit scripts in top 5 <- what runs when a gadget is submitted", kn)
		mem = notes("which apps are installed")
		print("HIT " if mem[:1] == ["profile__apps"] else "MISS", "profile__apps first <- which apps are installed", mem)
	seconds, counts = timed_build(knowledge)  # drops the fixtures' chunks
	print(f"BUILD without the fixtures: {seconds:.1f} s, {counts}")


def main(site):
	frappe.init(site=site, sites_path=".")
	frappe.connect()
	frappe.set_user("Administrator")
	frappe.flags.in_test = True  # the refresh hook would queue builds for the fixtures
	from erpsmith import knowledge, memory

	before = rss_mb()
	started = time.time()
	knowledge.search_erpnext_knowledge("warm up")
	first = time.time() - started
	after = rss_mb()
	for query, kind, want in CASES:
		titles = [r["title"] for r in knowledge.search_erpnext_knowledge(query, topK=5, kind=kind)["results"]]
		print("HIT " if want in titles else "MISS", kind, want, "<-", query, titles)
	started = time.time()
	memory.search_memory("can I import json in a server script")
	memory_ms = (time.time() - started) * 1000
	started = time.time()
	knowledge.search_erpnext_knowledge("custom field on a doctype")
	knowledge_ms = (time.time() - started) * 1000
	print(f"RSS {before} MB before, {after} MB after the first search; first knowledge search {first:.2f} s; "
		f"warm memory search {memory_ms:.0f} ms; warm knowledge search {knowledge_ms:.0f} ms")
	site_cases(knowledge, memory)


if __name__ == "__main__":
	main(sys.argv[1])
