# Plan 3 results: tool port

## Outcome
- Tools registered: 59 (tools.json and IMPLS); every one called over MCP by `test_all_tools` on v15 and v16.
- Tests: 333 on Frappe 15.123.1 and 333 on 16.52.0, all OK (`dev/test.sh` output, 2026-10-16). The plan expected 331; two more cases came from a task review (see below).

## Decisions taken (from the plan)
1. Script sanitizer rebuilt from probes, not ported verbatim: confirmed. It uses `ast`, and its test runs every case through the real sandbox on both benches, so a rule that stops being true fails CI.
2. Node field-name bugs corrected, not ported (Server Script `disabled`, Client Script `view`, Script Report `report_script`, Print Format `custom_format`, Dashboard Chart `type`, Notification `channel`, Workflow Document State without `style`): confirmed; `test_all_tools` creates each of these through the tools.
3. Standard DocTypes protected (`delete_doctype`, `rename_doctype`, `remove_field`; `update_field` writes Property Setters): confirmed; the smoke test only touches "ERPSmith Test ..." DocTypes.
4. Permission rules through `frappe.permissions`, one shared `builders.grant`, explicit System Manager check: confirmed on both benches.
5. `create_doctype` gives only System Manager access: confirmed; widening access is one `set_permission` call.
6. Gating derived from the annotations (`registry.hints`, `guards.is_mutating`), a tool whose name matches no rule fails at startup: confirmed; `test_all_tools` runs every write under its own plan and every read without one.
7. ERPNext not installed on the dev benches: confirmed; no tool needed an ERPNext DocType. The ERPNext-only knowledge probes still wait for the Plan 4 release smoke.
8. Deferred (raw database error logging, plan pruning, keyword stage for knowledge search, `MCP Plan Change.args` plaintext): nothing in the run changed these.
9. Logs return the last 1,000 characters of a traceback: confirmed (`get_error_logs`, `get_scheduler_logs`).

## Found during the run
- `frappe.client.set_value` returns a `frappe._dict`, and `hasattr(x, "as_dict")` is true on one, so `call_method` tested the wrong thing; it now checks `isinstance(result, Document)`. Seen on v15 and v16. Only the all-tools test found it.
- A task review found that the sandbox also rejects `frappe.format`; it is now a FAILS case in the sanitizer test. One-line compound imports are flagged rather than rewritten.
- Those two review changes are why the totals are 333, 2 above the plan's 331.
- `frappe.desk.reportview.delete_items` (the list view's bulk delete) was not covered by the bulk-delete guard, and the `names` check never fired; the guard now reads its JSON `items`. A JSON-string `fieldname` in `frappe.client.set_value` is read the way Frappe reads it, and odd argument shapes no longer crash the guard.

## For Plan 3b / 4
- Plan 3b: the customization index can reuse the reads in debug.py and builders.py; the knowledge keyword stage (Decision 8).
- Plan 4: raw DB error text (Plan 1 M3), plan pruning (M8), `MCP Plan Change.args` plaintext (owner), ERPNext-only knowledge probes (Decision 7).
