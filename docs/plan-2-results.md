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

**onnxruntime decision: no cap.** Every query matches on onnxruntime 1.31.0, the version that pip installs on both benches, so `pyproject.toml` keeps `onnxruntime>=1.20`. The planning spike also found the same top 3 on 1.23.2. The criterion is met without restricting the runtime, so no cap is applied.

## Drift context

The tokenizer gives token ids identical to the Node server's, so any difference comes from the runtime and not from the input. Python vectors drift slightly from Node's on longer notes (cosine stays at or above 0.998 on the reference texts; score gaps against Node are 0.0026 to 0.0240 on the ten queries), because onnxruntime 1.27 and later compute the dynamically quantized matmuls a little differently from the 1.14 that Node ships. Every top-3 flip seen while planning happened only where the Node server's own #3 and #4 results are within about 0.003 of each other, which is why the reference queries were chosen with a margin of at least 0.02.
