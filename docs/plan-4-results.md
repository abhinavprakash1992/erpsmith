# Plan 4 results: release

## Outcome
- Version 1.0.0 (`erpsmith/__init__.py`, `pyproject.toml`, CHANGELOG). The annotated tag `v1.0.0` is local; nothing is pushed.
- `dev/test.sh`: **406 tests OK on Frappe 15.123.1 and 406 OK on 16.52.0** (2026-10-16; Plan 3b ended at 372). The suite still runs on the Frappe-only sites `test15.localhost` / `test16.localhost`; ERPNext was added to the benches (it is in each bench's `apps.txt`) without affecting that run.
- The live ERPNext smoke found one defect, fixed in its own commit (below).

## Fresh install (Task 3, `dev/fresh_install.sh`, rerun on 2026-10-16 after ERPNext was added to the benches)
A throwaway site with only `erpsmith` installed, then dropped:

| | v15 (`fresh15.localhost`) | v16 (`fresh16.localhost`) |
|---|---|---|
| Result | `seed notes: 96, tools: 59` / `FRESH INSTALL OK bench15` | `seed notes: 96, tools: 59` / `FRESH INSTALL OK bench16` |

## ERPNext dev sites
`dev/setup-erpnext.sh bench15|bench16` (idempotent; a rerun migrates and changes nothing else):
`bench get-app erpnext --branch version-15|version-16`, a new site `erp15.localhost` / `erp16.localhost` with `install-app erpnext erpsmith`, then ERPNext's own programmatic setup (`erpnext.setup.setup_wizard.setup_wizard.setup_complete`: presets, fiscal year, company, defaults) for the company `ERPSmith Demo Co` (abbreviation `EDC`, India, INR, Standard chart of accounts), and `System Settings.setup_complete = 1`.

A bare `Company` insert does not work: it fails with `Could not find Warehouse Type: Transit` because the presets (Warehouse Type, Item Group, Customer Group, Territory, UOM) only come from the wizard stages. `setup_complete` is the code the wizard runs, without its user and sample-data steps. The Python on v15 is 3.12, on v16 3.14; ERPNext `version-15` / `version-16` at the branch heads of 2026-10-16. HRMS is not installed.

## Smoke (`dev/erpnext_smoke.py`)
Run as Administrator through `erpsmith.tests.helpers.run/call`, i.e. `mcp.dispatch` with an approved one-step plan per write:
`cd ~/frappe-bench/sites && ~/frappe-bench/env/bin/python /workspace/erpsmith/dev/erpnext_smoke.py erp15.localhost`

### Knowledge build with the manual (`knowledge.build()`, first build on an empty index)
| | v15 | v16 |
|---|---|---|
| Wall time | 228.5 s | 226.7 s |
| Chunks added (DocType + customization + manual) | 3162 | 3218 |
| Manual pages fetched | 888 | 888 |
| Profile notes | 7 | 6 first run (the warehouses note was missing: the defect below), 7 after the fix |
| Rebuild, nothing changed | 2.1 s, 3162 unchanged | 7.6 s, 3218 unchanged (and the one note the fix added) |

### Probes (top 3 by title)
| Query | v15 | v16 |
|---|---|---|
| `Sales Invoice Item` | Sales Invoice Item, Sales Invoice, Sales Invoice: OK | same: OK |
| `Purchase Receipt` | Purchase Receipt x3: OK | same: OK |
| `Leave Application` | not applicable: HRMS not installed | same |

### Profile notes read real ERPNext field names
Company `name`, `abbr`, `country`, `default_currency`, `parent_company`; Fiscal Year dates; Currency `enabled`; Global Defaults `default_currency`; Price List `currency`; Warehouse `is_group`, `parent_warehouse`; Stock Settings `valuation_method` all exist and are read on both versions. Output (identical on v15 and v16 after the fix):
- `profile__companies`: "This site has 1 company. Current fiscal year: 2026. - ERPSmith Demo Co (EDC), country India, currency INR" OK
- `profile__currencies`: "Default currency (Global Defaults): INR. Enabled currencies: AED, AUD, CHF, CNY, EUR, GBP, INR, JPY, USD. Currencies used by Price Lists: INR." OK
- `profile__warehouses`: "This site has 1 group warehouse. Default warehouse: Stores - EDC. Valuation method: FIFO. - All Warehouses - EDC: 4 child warehouses" OK (the note lists group warehouses and counts the four children: Stores, Work In Progress, Finished Goods, Goods In Transit)

### Tool smoke
| Check | v15 | v16 |
|---|---|---|
| `get_doctype` Sales Invoice | OK | OK |
| `create_record` Customer | OK | OK |
| `create_record` Item | OK | OK |
| `create_record` Sales Invoice (draft, 2 x 100, total 200) | OK | OK |
| `full_health_check` Sales Invoice (no failed check) | OK | OK |
| `debug_data_integrity` Sales Invoice | OK | OK |
| `search_erpnext_knowledge` "sales invoice" (Sales Invoice first) | OK | OK |

`SMOKE OK` on both. The script removes its Customer, Item and draft invoice at the start of the next run.

## Defect found and fixed
`a7c9b0c fix: warehouses note reads the default warehouse from Company on v16 (found by the ERPNext smoke)`: ERPNext v16 removed `Stock Settings.default_warehouse` (it is `Company.default_warehouse` there). `profile._warehouses` read the missing field, raised, and `profile_notes` logged it and skipped the whole `profile__warehouses` note, so a v16 site had no warehouses note at all (6 notes instead of 7). The note now reads Stock Settings where it has the field and each Company's default warehouse otherwise. Regression: `test_the_default_warehouse_comes_from_the_company_when_stock_settings_has_none` (the gatherer's reads are patched, since a Frappe-only site has no Company), and the smoke's warehouse check, which failed on v16 before the fix. Every other field name the Plan 3b formatters assumed was right.

## Decisions
1. Plan args stay in plaintext in `MCP Plan Change.args`: confirmed, unchanged; PRIVACY.md says so.
2. DocType metadata readable by any signed-in user: confirmed, unchanged; recorded in PRIVACY.md and README.
3. The dangerous-op guard covers User security fields through `call_method`: confirmed, unchanged (Task 1).
4. ERPNext dev sites added, test sites Frappe-only: confirmed. Changed within it: the company comes from ERPNext's `setup_complete`, not a bare insert (see above), and `dev/test.sh` still passes with ERPNext in the benches.
5. Version 1.0.0 with a CHANGELOG, tag local only: confirmed.
6. Deferred items: below.

## Later (Decision 6, after release)
- Frappe Cloud nginx check of `/.well-known/oauth-*` (spec risk 3): needs a Frappe Cloud site; the owner.
- The onboarding prompt as an MCP prompt (addendum Q4); the README carries it as text for 1.0.0.
- Scrubber edge cases from Plan 3b.
- Merging the six background-job seed notes (owner).
- Observed, not acted on: the repeat build on v15 rewrote one profile note (not identified; the smoke had added records, and the naming-series and apps notes come from InnoDB row estimates, see Plan 3b). Harmless.
