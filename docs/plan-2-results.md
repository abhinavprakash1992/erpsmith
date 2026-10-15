# Plan 2 results

## Memory parity (spec criterion 5)

Run on 2026-10-15 with `dev/test.sh erpsmith.tests.test_parity`. The test imports the owner's full Node memory (89 notes) inside its own transaction on a v15 site (and, as a second check, on a v16 site) and compares the top 3 of ten reference queries with the Node server's top 3. Both benches pass.

| Bench | Frappe | Python | onnxruntime | tokenizers | numpy |
|---|---|---|---|---|---|
| v15 (`test15.localhost`) | 15.123.1 | 3.12.15 | 1.31.0 | 0.23.3 | 2.5.4 |
| v16 (`test16.localhost`) | 16.52.0 | 3.14.8 | 1.31.0 | 0.23.3 | 2.5.4 |

Result on v15 and on v16 (the two tables are identical): **10 of 10 queries return the same top 3 in the same order as the Node server.** `SAME` means same notes in the same order, `SET` the same notes in another order, `DIFF` a different set. The max score diff is the largest gap between a Python cosine score and the Node score over the three results.

| Result | Max score diff | Query |
|---|---|---|
| SAME | 0.0050 | can I import json in a server script |
| SAME | 0.0066 | which function calls are banned in server scripts |
| SAME | 0.0043 | inserting child table rows from a server script |
| SAME | 0.0240 | send an outgoing HTTP request from a server script |
| SAME | 0.0140 | tuple unpacking error in a server script |
| SAME | 0.0094 | script report return convention columns and data |
| SAME | 0.0026 | error when rename_doc is called from a server script |
| SAME | 0.0040 | run a query with dynamic SQL in a server script |
| SAME | 0.0089 | why does the script editor cut off a long pasted script |
| SAME | 0.0205 | background job failure monitor using the error log |

The queries are the ten in `dev/parity-queries.json`. Each was kept only if the Node score gap between its #3 and #4 result (the margin) is at least 0.02; the measured margins run from 0.022 to 0.043, and the test asserts that bound, so a change in the owner's memory that makes a query tie-prone fails the test instead of silently weakening it. Note ids and titles are left out of this document on purpose, because they contain customer words.

**Mutation check.** With the L2 normalisation removed from `vectors.embed` the same test fails on both benches (8 of 10 queries come back `DIFF`, max score diffs 4 to 17), so the comparison can fail. The edit was reverted before this run.

The parity test skips wherever `private/` is absent (CI and other machines), so an onnxruntime upgrade needs a manual re-run of `erpsmith.tests.test_parity`.

**onnxruntime decision: no cap.** Every query matches on onnxruntime 1.31.0, the version that pip installs on both benches, so `pyproject.toml` keeps `onnxruntime>=1.20`. The planning spike also found the same top 3 on 1.23.2. The criterion is met without restricting the runtime, so no cap is applied.

## Drift context

The tokenizer gives token ids identical to the Node server's, so any difference comes from the runtime and not from the input. Python vectors drift slightly from Node's on longer notes (cosine stays at or above 0.998 on the reference texts; score gaps against Node are 0.0026 to 0.0240 on the ten queries), because onnxruntime 1.27 and later compute the dynamically quantized matmuls a little differently from the 1.14 that Node ships. Every top-3 flip seen while planning happened only where the Node server's own #3 and #4 results are within about 0.003 of each other, which is why the reference queries were chosen with a margin of at least 0.02.

## Knowledge build

Real builds on 2026-10-15 with `bench --site testNN.localhost execute erpsmith.knowledge.build`, each as its own command inside the bench container. The numbers are the printed result and the `time` line.

| Bench | Build | added | updated | unchanged | deleted | manual | real time |
|---|---|---|---|---|---|---|---|
| v15 | first | 2,660 | 0 | 0 | 0 | 888 pages | 3m32s |
| v15 | second | 0 | 0 | 2,660 | 0 | 888 pages | 2.2s |
| v16 | first | 2,686 | 0 | 0 | 0 | 888 pages | 3m32s |
| v16 | second | 0 | 0 | 2,686 | 0 | 888 pages | 2.1s |

No skips: the manual line reads `888 pages` on all four runs. The stored chunks split into 275 `doctype` and 2,385 `manual` on v15 (v16 has 26 more chunks; the split by kind was not recorded on v16, so which kind they belong to is not measured). The second build reads every DocType and re-fetches the manual yet finishes in about 2 s, and every chunk is hash-skipped, so a re-run is cheap. The chunks stay in the dev sites' databases.

## Retrieval cases

`dev/probe_knowledge.py` on each bench, `topK=5`. The case list comes from the Node server's `knowledge.test.js`; the three cases that need ERPNext DocTypes ("Sales Invoice Item", "Purchase Receipt", "Leave Application") wait until Plan 3 puts ERPNext on the dev benches. Output is identical on v15 and v16:

```
MISS doctype Server Script <- python script that runs on document save ['Client Script', 'Custom DocPerm', 'Audit Trail', 'Document Share Key', 'Website Script']
HIT  manual Perpetual Inventory <- how to configure perpetual inventory accounts ['Perpetual Inventory', 'Migrate To Perpetual Inventory', 'Migrate To Perpetual Inventory', 'Accounting Of Inventory Stock', 'Accounting Of Inventory Stock']
HIT  manual Delivery Note <- create a delivery note from a sales order ['Delivery Note', 'Delivery Note', 'Installation Note', 'Delivery Note', 'Delivery Note']
```

**Finding (MISS).** An exact-name style query for the `Server Script` DocType does not return it in the top 5, although its chunk exists (one `MCP Knowledge Chunk` titled `Server Script`). The query describes what the DocType does ("python script that runs on document save") and the embedding ranks Client Script and Website Script above it. This is the case Decision 5 reserves a keyword stage for: it is recorded here and left for Plan 3 or 4 to decide, not fixed in this task.

## Footprint

Peak RSS of the probe process (`resource.getrusage`), measured around the first knowledge search (model load plus cache build over the stored chunks), and wall time of searches after that:

| Bench | RSS before | RSS after first search | First knowledge search | First memory search (includes building the memory cache) | Warm knowledge search |
|---|---|---|---|---|---|
| v15 | 74 MB | 176 MB | 0.56 s | 49 ms | 8 ms |
| v16 | 92 MB | 209 MB | 0.48 s | 30 ms | 6 ms |

The first search adds 102 MB (v15) and 117 MB (v16) of peak RSS, more than the roughly 75 MB expected for the model alone. The cached vectors do not explain the difference (2,700 x 384 float32 is about 4 MB); the cause beyond the model was not isolated, and onnxruntime's arena and session overhead is the likely part. All timings are single samples and the RSS figures are peak RSS (`resource.getrusage`), so read them as orders of magnitude. Warm knowledge searches are well under the "low tens of milliseconds" bar; the memory search figure is the first memory search in the process, so it includes building the memory cache and is not a warm timing.

## Seed memory (spec criterion 4)

`erpsmith/seed_memory.json` ships **36 notes**: 15 sandbox-rule, 18 pattern, 3 bug-fix. Built with `python3 dev/build_seed.py` from `private/seed-candidates.json`.

- Owner approval: 2026-10-15 ("approved all", no blocklist terms named). The controller read that as the 46 candidates suggested include (22) or review (24); the 43 suggested exclude (context and handoff topics, emails, foreign URLs, the company name) do not ship.
- Of the 46: 36 shipped, all reworded (directives restated as facts, dates, version labels, amounts, document ids, record counts, internal DocType and script names and vendor details removed). 5 were dropped as incident reports or instance-specific notes that lose their point once made generic. 5 more were dropped in review because their sandbox claims are false on v15 and v16.
- Every shipped note passes the hard rules (`seed_rules.hard_violations`) with the owner's private blocklist applied.
- The notes were checked against `safe_exec` on Frappe v15.123.1 and v16.52.0 by the reviewer's probe (rolled back), with identical results on both. About a third of the first draft misstated the sandbox: `datetime` is not a global; `b'...'` literals, ternaries with calls and `except AttributeError` work; child rows appended and saved from a Server Script persist; `frappe.rename_doc`, `File.get_content()` and `frappe.utils.generate_hash` are available. Those notes were corrected or dropped. Two corrections were re-probed for this round: a missing attribute on an ordinary object reads as `None`, and `EXPLAIN` / MariaDB `WITH` pass the read-only SQL check.
- RULE 1 in `erpsmith/instructions.md` and the import-error suggestion in `erpsmith/mcp.py` said `datetime` is available; both now say frappe and json are, with date helpers in `frappe.utils`. The spec's RULE 1 text carries the same error and is left for the owner.

After `bench migrate` on each dev site, `memory.load_seeds()` then `search_memory("can I import json in a server script", topK=3)`:

| Bench | `load_seeds()` after migrate | Top 3 (id, score) |
|---|---|---|
| v15 | `{'inserted': 0, 'already_present': 0}` | `seed__imports-raise-importerror-in-server-scripts-frappe` 0.5059, `seed__reading-uploaded-files-from-a-server-script` 0.3947, `seed__csv-uploads-the-server-returns-rows-the-client-per` 0.3581 |
| v16 | `{'inserted': 0, 'already_present': 0}` | same three ids, same scores |

The top hit is the sandbox-rule note on imports on both versions. `test_seed_content` asserts that exact id on a fresh install. (The dev sites' first-draft seed rows and the `erpsmith_seeds_applied` default were deleted before this migrate, so the corrected text loaded.)

## Deviations from the plan

- Plan 1's record tool descriptions were too terse for the new lint: `create_record` (31 characters), `get_record` (37) and `delete_record` (33) fall under its 40-character floor, and `update_record` (41) and `query_records` (44) only just clear it. All five were reworded in `tools.json` in descriptive voice, naming the signed-in user's permissions and, for the three write tools, the approved plan they need. No tool name, schema or argument changed.
- `instructions.md` needed no edit for the memory and knowledge tools: RULE 0 is true against the 15 registered tools (both memory and knowledge tools exist, the System Manager sentence is there, `add_memory` saves a note). Task 12 corrected RULE 1's claim that `datetime` is available (see "Seed memory"); the other Plan 3 items (RULE 1 sanitizer, RULE 13 drafts, RULE 16 over-claims, the `call_method_read` mention) are untouched.
- Task 12: the owner approved every candidate in the include and review buckets. 5 of those 46 were dropped while rewording because they could not be made generic, and 5 more in review because their sandbox claims are false (see "Seed memory" above). Approved notes carry new `seed_key` values derived from their reworded titles, because the original keys came from titles with dates and integration names.

## For Plan 3 / 4

- 15 tools are registered after Plan 2 (3 plan, 5 record, 6 memory, 1 knowledge), so Plan 3 starts from 15 of 59.
- Knowledge search: the `Server Script` MISS above says a keyword stage for exact DocType names is worth deciding on.
- The cap on `onnxruntime` was not applied (parity passes on 1.31.0), so there is nothing to carry.
- `README.md` gets a Memory and Knowledge section and the System Manager requirement in Plan 4.
- `PRIVACY.md` must say that memory notes and knowledge chunks are stored in the site database, readable by System Managers, and that the knowledge build fetches the public ERPNext manual from docs.erpnext.com.
- The `MCP Plan Change.args` plaintext decision (Plan 1 final review I4) is still the owner's before release.
- `erpsmith/embedding/NOTICE` should ship the Apache-2.0 licence text itself (Plan 1 Task 6 minor, Plan 4).
- Probe the three ERPNext-only retrieval cases once ERPNext is installed on the dev benches.
- The seed's sandbox claims were probed on v15 and v16 (see "Seed memory"). The background job and Payment Entry recipes are field observations from one site and were not exercised end to end. Merging the six bg_job_* notes into fewer recipe notes is deferred to the owner.
