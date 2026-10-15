#!/usr/bin/env python3
"""Retrieval cases, memory footprint and search latency on a dev site.

    cd ~/frappe-bench/sites && ../env/bin/python /workspace/erpsmith/dev/probe_knowledge.py test15.localhost
"""

import resource
import sys
import time

import frappe

CASES = [  # from the Node server's knowledge.test.js; the manual kind was called "doc" there
	("python script that runs on document save", "doctype", "Server Script"),
	("how to configure perpetual inventory accounts", "manual", "Perpetual Inventory"),
	("create a delivery note from a sales order", "manual", "Delivery Note"),
]


def rss_mb():
	peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
	return peak // (1024 * 1024) if sys.platform == "darwin" else peak // 1024


def main(site):
	frappe.init(site=site, sites_path=".")
	frappe.connect()
	frappe.set_user("Administrator")
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


if __name__ == "__main__":
	main(sys.argv[1])
