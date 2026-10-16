# Plan 3b results: site knowledge

## Outcome
- Tests: 372 on Frappe 15.123.1 and 372 on 16.52.0, all OK (`dev/test.sh`, 2026-10-16). Plan 3 ended at 338; Plan 3b adds the scrubber, customization chunks, ranking, refresh hook and profile tests.
- `search_erpnext_knowledge` gains the `kind` value `"customization"`; no tool name or argument changed.
- Knowledge index now holds, besides DocType and manual chunks: one chunk per Workflow, Server Script, Client Script, custom Notification and custom Print Format (secrets scrubbed from script code, no notification bodies or recipient addresses, no print format HTML). Seven generated profile notes live in MCP Memory (`profile__<key>`, topic `context`).

## Retrieval probes (`dev/probe_knowledge.py`, fixtures created and removed by the probe)
Fixtures: a submittable test DocType, a Workflow whose transition is allowed for the role `ERPSmith Test Approver`, two After Submit Server Scripts. Afterwards no test DocType, table, role, workflow or script remains on either site.

| Case | v15 | v16 |
|---|---|---|
| "who approves gadgets": workflow chunk or `profile__workflows` in the top 3 | HIT: workflow chunk first; `profile__workflows` first in memory | HIT: same |
| "what runs when a gadget is submitted": both After Submit scripts in the top 5 | HIT: both scripts at ranks 1 and 2 | HIT: same |
| "which apps are installed": `profile__apps` first (`search_memory`, topic `context`) | HIT | HIT |
| Plan 2 case "python script that runs on document save" -> `Server Script` | MISS (unchanged from Plan 2; the exact-name rule only helps a query that is the name) | MISS (same) |
| Manual cases (Perpetual Inventory, Delivery Note) | HIT, HIT | HIT, HIT |

Memory: 79 MB before and 177 MB after the first search on v15; 93 MB and 209 MB on v16. Warm searches 17 to 31 ms.

### Build timings (`knowledge.build(include_manual=False)`)
| | v15 | v16 |
|---|---|---|
| With the fixtures, first run (4 chunks and 5 profile notes embedded; the profile notes' first insert) | 4.5 s | 2.0 s |
| With the fixtures, repeat runs (4 new chunks, 1 updated, rest unchanged) | 0.3 to 0.4 s | 0.3 s |
| After the fixtures are removed (4 chunks deleted, nothing else changed in the index) | 0.2 s | 0.1 s |

Time is dominated by embedding changed chunks; scanning ~280 (v15) or ~300 (v16) unchanged chunks costs well under a second.

### Probe finding fixed in this task
The first probe run put `profile__footprint` ahead of `profile__apps` for "which apps are installed" on both benches (the unit test only asks for the top 3). The footprint note's first line said "Custom apps installed" and the apps note led with a bullet list. Wording changed: the footprint line now reads "Apps outside the standard set: ...", and the apps note opens with one sentence, "Apps installed on this site: <name version>, ...". This is the only code change under `erpsmith/` in Task 6.

## Decisions (from the plan)
1. Code in the index (scrubbed script code stored in the chunk): confirmed. "What runs when a gadget is submitted" finds both scripts.
2. A deleted or edited profile note returns on the next build: confirmed by test (`test_a_rerun_embeds_nothing_and_a_deleted_note_returns`).
3. Plan 3's `get_*_script` not scrubbed: confirmed, no change.
4. Onboarding prompt as an MCP prompt: no (README only, Plan 4).
5. Other sources: none in v1.
6. Keyword stage done in Task 3 (exact DocType names rank first): confirmed. It does not help a descriptive query such as the Plan 2 "Server Script" case, which still misses.
7. ERPNext not on the dev benches: confirmed. Companies, warehouses and the ERPNext currency defaults are built by pure formatters tested on synthetic rows; the live check moves to Plan 4. Changed within this decision: existence of a source is judged by the DocType record, not its table, because Single DocTypes (Global Defaults, Stock Settings) have no table.

## Review findings fixed during execution
- Scrubber: secrets written in other string forms (see the scrubber fix commit ce03096) are now scrubbed before a script reaches a chunk.
- Refresh: Frappe checks `deduplicate` when `frappe.enqueue` is called but pushes the job after commit, so N saves in one transaction pushed the job N times. `on_source_change` now queues once per transaction (a flag cleared on commit and on rollback).
- Single DocTypes: `_have` used `table_exists`, which is false for Single DocTypes, so ERPNext defaults would never appear. It now checks the DocType record.
- Naming series: the note listed every DocType with a naming series field. It now keeps DocTypes whose table holds rows, most rows first, capped at 40 lines.
- A profile note whose gatherer raises is logged and skipped instead of failing the build after the chunks were committed.

## For Plan 4
- README onboarding prompt (addendum §4).
- PRIVACY.md sentence about the index and the profile notes (addendum §5).
- Live ERPNext profile check (Decision 7): confirm the field names the formatters read (Company abbr, country, default_currency, parent_company; Warehouse is_group, parent_warehouse; Stock Settings default_warehouse, valuation_method; Global Defaults default_currency; Price List currency; Fiscal Year dates) on a real ERPNext site.
- The naming-series order and the apps note's module list come from InnoDB row estimates; a note may be rewritten (and re-embedded) when estimates shift. Harmless, but worth watching on a large site.
