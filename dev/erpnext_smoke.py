#!/usr/bin/env python3
"""Live smoke of ERPSmith on a real ERPNext site (Plan 4). Needs dev/setup-erpnext.sh to have run.

    cd ~/frappe-bench/sites && ../env/bin/python /workspace/erpsmith/dev/erpnext_smoke.py erp15.localhost

Prints one line per check, each ending `OK` or `FAIL <reason>`; exits 1 when any check failed. The Customer, Item and draft
Sales Invoice it makes are removed first on a re-run. The build fetches the ERPNext manual (one request to docs.erpnext.com).
"""

import json
import sys
import time

import frappe

COMPANY, ABBR, CURRENCY = "ERPSmith Demo Co", "EDC", "INR"
CUSTOMER, ITEM = "ERPSmith Smoke Customer", "ERPSMITH-SMOKE-ITEM"
failures = []


def report(label, problem=None):
	print(f"{label} ... " + ("OK" if not problem else f"FAIL {problem}"))
	if problem:
		failures.append(label)


def check(label, fn):
	"""Run fn(); it returns None or a problem string, or raises."""
	try:
		report(label, fn())
	except Exception as e:
		report(label, f"{type(e).__name__}: {str(e)[:300]}")


def titles(knowledge, query, **kw):
	return [r["title"] for r in knowledge.search_erpnext_knowledge(query, topK=3, **kw)["results"]]


def tool(helpers, name, args, write=False):
	"""One tool call as the endpoint makes it, under an approved plan for writes. Raises with the tool's error text."""
	is_error, out = (helpers.run if write else helpers.call)(name, args)
	if is_error:
		raise RuntimeError(out if isinstance(out, str) else json.dumps(out)[:300])
	return out


def clean_up():
	for name in frappe.get_all("Sales Invoice", filters={"customer": CUSTOMER}, pluck="name"):
		frappe.delete_doc("Sales Invoice", name, force=True)
	for dt, name in (("Customer", CUSTOMER), ("Item", ITEM)):
		if frappe.db.exists(dt, name):
			frappe.delete_doc(dt, name, force=True)
	# dev script, not app code: commits its fixtures so the separate steps and worker processes that follow see them
	frappe.db.commit()  # nosemgrep


def knowledge_section(knowledge):
	started = time.time()
	counts = knowledge.build()
	print(f"1. knowledge.build() with the manual: {time.time() - started:.1f} s, {counts}")
	check("1. build fetched the manual", lambda: None if counts["manual"].endswith("pages") else counts["manual"])
	for query, want in (("Sales Invoice Item", "Sales Invoice Item"), ("Purchase Receipt", "Purchase Receipt")):
		top = titles(knowledge, query)
		print(f"2. top 3 <- {query}: {top}")
		check(f"2. {query} first", lambda: None if top[:1] == [want] else f"got {top}")
	print("2. Leave Application ... not applicable: HRMS not installed")


def profile_section():
	def note(key):
		return frappe.get_doc("MCP Memory", f"profile__{key}").content if frappe.db.exists("MCP Memory", f"profile__{key}") else None

	# The note lists group warehouses and the stock default (the leaf warehouses under a group are only counted).
	warehouses = frappe.get_all("Warehouse", filters={"company": COMPANY, "is_group": 1}, pluck="name")
	# v15 keeps the default warehouse in Stock Settings, v16 on the Company.
	warehouses.append(frappe.db.get_single_value("Stock Settings", "default_warehouse") if frappe.get_meta("Stock Settings").has_field("default_warehouse")
		else frappe.db.get_value("Company", COMPANY, "default_warehouse"))
	for key, wants in (("companies", [COMPANY, ABBR, CURRENCY, "India"]), ("currencies", [CURRENCY]), ("warehouses", warehouses)):
		text = note(key)
		print(f"3. profile__{key}:\n" + (text or "(missing)").replace("\n", "\n     "))
		check(f"3. profile__{key} names {', '.join(wants)}",
			lambda: "note missing" if text is None else (f"missing {[w for w in wants if w not in text]}" if not all(w in text for w in wants) else
				"no warehouses on the company" if not all(wants) else None))


def tools_section(helpers):
	check("4. get_doctype Sales Invoice", lambda: None if tool(helpers, "get_doctype", {"name": "Sales Invoice"})["fieldCount"] > 50 else "few fields")
	tool(helpers, "create_record", {"doctype": "Customer", "data": {"customer_name": CUSTOMER, "customer_type": "Company"}}, write=True)
	check("4. create_record Customer", lambda: None if frappe.db.exists("Customer", CUSTOMER) else "not created")

	def item():
		tool(helpers, "create_record", {"doctype": "Item", "data": {"item_code": ITEM, "item_name": ITEM, "item_group": "All Item Groups",
			"stock_uom": "Nos", "is_stock_item": 0}}, write=True)
	check("4. create_record Item", item)

	def invoice():
		out = tool(helpers, "create_record", {"doctype": "Sales Invoice", "data": {"company": COMPANY, "customer": CUSTOMER,
			"items": [{"item_code": ITEM, "qty": 2, "rate": 100}]}}, write=True)
		doc = frappe.get_doc("Sales Invoice", out["name"])
		return None if doc.docstatus == 0 and doc.grand_total == 200 else f"docstatus {doc.docstatus}, total {doc.grand_total}"
	check("4. create_record Sales Invoice (draft, 2 x 100)", invoice)

	def health():
		out = tool(helpers, "full_health_check", {"doctype": "Sales Invoice"})
		return None if not out["failedChecks"] else f"checks raised: {out['failedChecks']}"
	check("4. full_health_check Sales Invoice", health)
	check("4. debug_data_integrity Sales Invoice", lambda: None if "error" not in tool(helpers, "debug_data_integrity", {"doctype": "Sales Invoice"}) else "error")

	def search():
		top = [r["title"] for r in tool(helpers, "search_erpnext_knowledge", {"query": "sales invoice"})["results"]]
		return None if top[:1] == ["Sales Invoice"] else f"got {top}"
	check('4. search_erpnext_knowledge "sales invoice" (Sales Invoice first)', search)


SITES = ("erp15.localhost", "erp16.localhost")  # it creates and deletes records, so it runs on the dev benches only


def main(site):
	if site not in SITES:
		print(f"refusing to run on {site}: this smoke creates and deletes records and runs only on {' or '.join(SITES)}")
		return 2
	frappe.init(site=site, sites_path=".")
	frappe.connect()
	frappe.set_user("Administrator")
	frappe.flags.in_test = True  # the refresh hook would queue builds for the smoke's own records
	from erpsmith import knowledge
	from erpsmith.tests import helpers

	print(f"ERPSmith smoke on {site}: frappe {frappe.__version__}, apps {frappe.get_installed_apps()}")
	knowledge_section(knowledge)
	profile_section()
	clean_up()
	tools_section(helpers)
	# dev script, not app code: commits its fixtures so the separate steps and worker processes that follow see them
	frappe.db.commit()  # nosemgrep
	print("SMOKE FAILED: " + "; ".join(failures) if failures else "SMOKE OK")
	return 1 if failures else 0


if __name__ == "__main__":
	sys.exit(main(sys.argv[1]))
