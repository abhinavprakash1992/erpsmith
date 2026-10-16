# Claude Directory listing

Listing type: **custom connection**. Users enter their own site's MCP address at connect time (`https://<their-site>/api/method/erpsmith.mcp.handle`); there is no shared endpoint, so the server URL differs for every site.

## Name

ERPSmith

## Tagline

AI dev copilot for ERPNext: build and debug customizations behind a plan gate

## Description

ERPSmith is a Frappe app that adds an MCP endpoint to an ERPNext site (Frappe v15 and v16). Claude signs in as the user with OAuth and can then read, build, debug and fix DocTypes, fields, client and server scripts, workflows, permissions, print formats, reports, notifications and dashboard charts. It has semantic memory of Frappe script-sandbox facts and patterns, an index of the site's own customizations, and a generated site profile.

Every tool runs as the signed-in user, with that user's roles and permissions. Tools that change something are rejected until a plan listing the exact changes has been submitted and approved by the user. Free and open source (MIT), written by Abhinav Prakash.

Not affiliated with or endorsed by Frappe Technologies.

## Who it is for

Developers, implementers and administrators of ERPNext sites who install the app on their own site or bench.

## Authentication

OAuth 2.0 with PKCE and dynamic client registration, against the user's own site. On Frappe v16 the framework provides it; on v15 the app adds the discovery documents and client registration. No API keys.

## How the plan gate works

Tools marked read-only are never gated. Tools that write wait for a plan: `submit_plan` records the intended changes and returns a planId; the user reviews the plan; after `approve_plan` the listed changes can run for 15 minutes, each once and with exactly the arguments in the plan. Only the user who made a plan can approve it. A few operations (changing a User's roles, password or API keys, deleting protected DocTypes, deleting more than 10 records in one call) are rejected even with an approved plan.

## Tools (59)

Every tool has a title and a readOnlyHint or destructiveHint annotation.

### Read-only (25)

- `get_current_plan`: Returns your latest plan: its status, the approved changes and which of them have been used.
- `get_record`: Returns one record from any DocType by name, as the signed-in user, including its child tables.
- `query_records`: Returns a list of records from any DocType, filtered and limited, as the signed-in user.
- `search_memory`: Semantic search over this site's saved reference notes: Frappe sandbox facts, working code patterns, past bug fixes, site context and handoff notes.
- `list_memory`: Lists saved notes (id, topic, title, created date), newest first, without semantic search.
- `dump_memory`: Returns every saved note as one markdown document grouped by topic, for backup, review or committing to git.
- `search_erpnext_knowledge`: Semantic search over three sources built for this site: the structure of every DocType on the site (fields, types, required flags, child tables, what each links to and what links to it, custom fields and custom apps included, plus the workflow, scripts, notifications and print formats attached to it), this site's own workflows, server and client scripts, notifications and print formats (kind customization), and the ERPNext user manual.
- `get_doctype`: Returns a DocType's settings, every field (custom fields and property overrides included) and the permission rules in force.
- `list_doctypes`: Lists DocTypes by name, optionally filtered by module or by a fragment of the name, showing whether each is custom, a child table, single or submittable.
- `list_fields`: Lists every field of a DocType in form order, custom fields included, with type, options, required, hidden and default.
- `list_client_scripts`: Lists Client Scripts with their DocType, view and enabled state, optionally for one DocType.
- `get_client_script`: Returns a Client Script's full JavaScript with its DocType, view and enabled state.
- `list_server_scripts`: Lists Server Scripts with their type, DocType, event, API method, schedule and enabled state, optionally filtered by DocType or script type.
- `get_server_script`: Returns a Server Script's full Python code with its type, DocType, event, API method, schedule and enabled state.
- `list_workflows`: Lists Workflows with their DocType and whether each is active, optionally for one DocType.
- `full_health_check`: Runs every check on one DocType at once (fields, client scripts, server scripts, permissions and its workflow) and returns each check's issues and suggested fixes.
- `debug_client_script`: Checks a Client Script, or every client script of a DocType, for a missing frappe.ui.form.on wrapper, unbalanced braces, handlers without frm, deprecated APIs, console.log and disabled state, with fixes where one is mechanical.
- `debug_server_script`: Checks a Server Script, or every server script of a DocType, against the sandbox's real limits and for mixed indentation, raw SQL, empty frappe.throw, bare except, recent Error Log entries from it and disabled state; also reports when the bench has server scripts turned off.
- `debug_doctype_fields`: Checks a DocType's fields for Link and Table fields pointing at missing DocTypes, empty Select options, depends_on conditions naming missing fields, broken Dynamic Links, duplicate or invalid fieldnames, and a submittable DocType nobody may submit.
- `debug_workflow`: Checks a Workflow for being inactive, missing states or transitions, transitions to undefined states, unreachable states, actions open to every user, and draft documents stuck in a state the workflow no longer has.
- `debug_permissions`: Checks the permission rules in force on a DocType (Custom DocPerm rows replace its own once any exist) for a DocType nobody can open, a submittable DocType nobody may submit, rules that are all read-only, and optionally what one role may do.
- `debug_data_integrity`: Scans a DocType's most recent records for empty required fields and Link values whose linked record no longer exists.
- `get_error_logs`: Returns recent Error Log entries, newest first, filtered by method name, DocType or date, with the end of each traceback.
- `get_scheduler_logs`: Returns recent Scheduled Job Log entries, newest first, with each job's status and the end of its details.
- `call_method_read`: Calls a whitelisted Frappe or ERPNext Python method, the same methods the REST API serves at /api/method/<dotted.path> (https://docs.frappe.io/framework/user/en/api/server-calls), as the signed-in user, and rolls back every database change it makes; a method that commits or queues a background job is refused, and rolledBack is false if the database could not be fully rolled back.

### Plan tools (2, not gated)

- `submit_plan`: Records the write operations you intend to make (every tool that is not read-only, apart from the plan and memory tools) and returns a planId.
- `approve_plan`: Approves a submitted plan so its writes can run, for ttlMinutes (default 15).

### Memory writes (3, not gated: they write the site's own notes)

- `add_memory`: Saves a note (topic, title, content) so that search_memory can find it later.
- `update_memory`: Edits an existing note by id (from search_memory or list_memory) and re-embeds it so search keeps finding it.
- `delete_memory`: Deletes a note by id (from search_memory or list_memory).

### Writes behind the plan gate (29)

- `create_record`: Creates a record in any DocType using the signed-in user's permissions; a submittable document stays a draft unless data sets docstatus to 1.
- `update_record`: Updates fields on an existing record in any DocType using the signed-in user's permissions.
- `delete_record`: Deletes one record from any DocType using the signed-in user's permissions.
- `create_doctype`: Creates a new custom DocType (stored in the database, custom=1) with its fields, naming rule and permissions; System Manager gets full access.
- `delete_doctype`: Deletes a custom DocType (custom=1) with its custom fields, property setters and permission rules; standard DocTypes are refused.
- `rename_doctype`: Renames a custom DocType (custom=1) and its database table, updating links to it; standard DocTypes are refused.
- `add_field`: Adds a field to a DocType.
- `update_field`: Changes properties of an existing field (label, reqd, hidden, default, options, depends_on and so on).
- `remove_field`: Removes a Custom Field, or a field of a custom DocType.
- `reorder_fields`: Changes the form order of a DocType's fields with a field_order Property Setter, as Customize Form does.
- `set_property`: Overrides one property of a DocType or of one of its fields with a Property Setter, without editing the DocType.
- `create_client_script`: Creates a Client Script: JavaScript that runs in the browser on a DocType's form or list view, e.g.
- `update_client_script`: Replaces a Client Script's code, or enables or disables it.
- `create_server_script`: Creates a Server Script: Python run by Frappe's sandbox on a DocType event, as an API method at /api/method/<apiMethod>, on a schedule, or as a permission query.
- `update_server_script`: Replaces a Server Script's code (checked against the sandbox's limits first), changes its DocType event, or enables or disables it.
- `apply_script_fix`: Replaces the code of a Client Script or Server Script with a corrected version and, by default, enables it.
- `create_workflow`: Creates and activates a Workflow for a DocType from states and transitions, e.g.
- `set_permission`: Sets a role's permission rule on a DocType (read, write, create, delete, submit and so on) the way the Role Permission Manager does, keeping every other role's rules.
- `create_print_format`: Creates a custom Print Format for a DocType from a Jinja HTML template (values as {{ doc.field_name }}) and optional CSS.
- `create_report`: Creates a custom report: a Script Report (Python, checked against the sandbox's limits like a Server Script), a Query Report (one SQL SELECT) or a Report Builder report.
- `create_notification`: Creates an automatic Notification (email, system notification, Slack or SMS) sent when a DocType's documents are created, saved, submitted, cancelled, change a value, or reach a date.
- `create_dashboard_chart`: Creates a public Dashboard Chart over a DocType's documents: grouped by a field (groupBy), or counted over time by a date field (default creation).
- `fix_broken_link`: Points a Link or Table field that names a missing DocType at the right DocType, by changing the field's options where they live (Custom Field, custom DocType or Property Setter).
- `fix_stuck_document`: Moves a document stuck in a workflow state that no longer exists to a valid state of its active workflow, without running the transition check, and records the change in the document's timeline.
- `activate_workflow`: Activates a Workflow so it applies to its DocType's documents; Frappe deactivates the DocType's other workflows, and the result names them.
- `fix_permission`: Sets a role's permission rule on a DocType, as set_permission does, keeping every other role's rules.
- `fix_broken_link_value`: Sets a record's Link field, whose linked record was deleted, to an existing record (or clears it with an empty value), saving the record with its usual validation.
- `fix_missing_required_value`: Sets an empty required field on a record and saves it with its usual validation.
- `call_method`: Calls a whitelisted Frappe or ERPNext Python method, the same methods the REST API serves at /api/method/<dotted.path> (https://docs.frappe.io/framework/user/en/api/server-calls), with keyword arguments, as the signed-in user, and keeps its database changes.

## Data handling

Everything is stored in the site's own database. No telemetry. Tool results reach the model provider of the client in use. The only outside request the app makes is a fetch of the public ERPNext manual from docs.erpnext.com. Policy: PRIVACY.md in the repository (https://github.com/abhinavprakash1992/erpsmith/blob/main/PRIVACY.md).

## Reviewer test account

Prepare a demo site before submitting (owner action), on a private bench with `server_script_enabled` set to 1:

1. A site with ERPNext installed and ERPSmith installed, with some synthetic data (company "ERPSmith Demo Co", a few items and customers).
2. A user `reviewer@demo.example` with these roles only: System Manager (the memory and knowledge tools need it), plus Customer and Item read/write roles such as Sales User and Stock User. This is a throwaway demo site, so its data may be changed freely.
3. Give the reviewers the site's MCP address and the user's password through the submission form, not in this file.

Prompts to try, in order:

- "Search ERPSmith memory: can I import json in a server script?" (read-only; the first result is the import rule)
- "List the custom fields on Customer." (read-only)
- "Add a Data field called Preferred Contact Time to Customer." (the assistant submits a plan; the reviewer approves it; the field is created)
- "Create a server script that sets a Customer's territory before save." (shows the sandbox check and the plan gate)
- "Run a full health check." (read-only)

Expected: without an approved plan a write tool returns a short message saying it is waiting for approval; after approval it succeeds, and the change shows in Desk under MCP Plan.

Not affiliated with or endorsed by Frappe Technologies.
