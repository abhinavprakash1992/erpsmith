# ERPSmith

**AI dev copilot for ERPNext.** Build and debug DocTypes, scripts and workflows from Claude over the Model Context Protocol, behind a plan gate, with semantic memory. Frappe v15 and v16. Free and open source (MIT).

Not affiliated with or endorsed by Frappe Technologies. ERPNext and Frappe are trademarks of Frappe Technologies Pvt. Ltd.

## What it is, and who it is for

ERPSmith is a Frappe app that adds an MCP endpoint to your site. An MCP client such as Claude connects to it, signs in as one of your users, and can then read, build, debug and fix customizations: DocTypes and fields, client and server scripts, workflows, permissions, print formats, reports, notifications and dashboard charts (59 tools in all).

It is for developers, implementers and administrators who customize ERPNext sites. Every tool runs as the signed-in user, with exactly that user's roles and permissions. Every change to your site's records and customizations waits for a plan you approve (see "The plan gate"); only memory notes are not gated. It also remembers Frappe sandbox facts, working patterns and your company's own context, so the assistant stops repeating mistakes.

## Install

On a bench (Frappe v15 or v16; the app requires Python 3.11 or newer):

```bash
bench get-app https://github.com/abhinavprakash1992/erpsmith
bench --site your-site.example install-app erpsmith
bench --site your-site.example migrate
```

Installing puts the seed notes in place and queues the knowledge build (a few minutes, in the long queue).

On Frappe Cloud, install it from the Marketplace. Server script tools only work when server scripts are enabled, which on Frappe Cloud needs a private bench with `server_script_enabled` set to 1 (`bench set-config -g server_script_enabled 1` on your own bench). Without it the script tools say so and everything else works.

## Connect Claude

In Claude, add a custom connector and enter your site's MCP address, `https://<your-site>/api/method/erpsmith.mcp.handle`. You enter nothing else: no key, no secret.

Claude finds the sign-in page by itself (OAuth 2.0 with PKCE and dynamic client registration). On Frappe v16 that is built into the framework. On v15 ERPSmith supplies the missing pieces (the `/.well-known/oauth-*` documents and client registration). You sign in on your own site and approve access, and Claude acts as that user.

### Use a dedicated "AI" user

Create a user such as `ai@your-company.example` that has only the roles the assistant should have, and sign in as that user when you connect. The assistant can then do exactly what those roles allow and no more. Roles that can change DocTypes, scripts and workflows are the sensitive ones; leave them off a user you do not want building things.

## The plan gate

Every tool that changes something is rejected until a plan covering it has been submitted, shown to you and approved. The assistant calls `submit_plan` with the exact changes it intends (tool, arguments, reason), you read the plan in Claude and say yes, and the assistant then calls `approve_plan` as you. Approval lasts 15 minutes by default (up to 24 hours). Each change must match a plan entry exactly and can be used once. You can only approve your own plans. Plans are listed read-only in Desk (MCP Plan) as an audit trail, deleted by a daily scheduled job once they are more than 7 days old and can no longer unlock anything. Read-only tools, the plan tools and the three tools that write memory notes (`add_memory`, `update_memory`, `delete_memory`) are not gated. A few operations are blocked even with an approved plan: changing a User's roles (including the Has Role rows behind them), password or API keys, generating API keys, deleting records of protected DocTypes such as User or Company, and deleting more than 10 records in one call.

## Memory, knowledge and the site profile

- **Memory** (`search_memory`, `add_memory`, ...): notes the assistant searches by meaning. 96 notes ship with the app (Frappe sandbox rules, working patterns, known fixes); you and the assistant add your own. Notes are shared across the site and readable by System Managers.
- **Knowledge** (`search_erpnext_knowledge`): an index of your site's DocType structure (custom fields included), workflows, scripts, notifications and print formats, plus the ERPNext user manual. It is built in the background after install and migrate and refreshed when those records change. Script code in the index has secrets scrubbed on a best-effort basis; keep secrets in password fields or site config, not in scripts.
- **Site profile**: short generated notes (topic `context`) on your companies, currencies, warehouses, naming series, installed apps, workflows and customization footprint. Edits to these generated notes are overwritten on the next build.

Memory, the knowledge index and the site profile need the System Manager role to read or change.

## Teach ERPSmith how your company works

Paste this into Claude once, after connecting. It makes the assistant interview you and save what it learns as notes the next conversation can find.

```text
Use ERPSmith to learn how my company works.
1. Start with search_memory (topic "context") to read what is already known: the site profile and any earlier answers.
2. Then ask me one question at a time about: what the company does and sells; order-to-cash; procure-to-pay and approval limits; stock (which warehouses mean what, batches, serials, valuation); manufacturing or projects if we use them; who does what (roles tied to jobs, not names); month-end and tax filing; integrations and imports; local terms and abbreviations; things that went wrong before.
3. After each area, show me a short summary written as third-person facts ("Purchase Orders above 50,000 INR need Finance Manager approval in the PO Approval workflow."), not as instructions. Only after I confirm, save it with add_memory(topic="context", title="Business: <area>", content=...).
4. Leave out passwords, bank details and personal data: notes are stored in the site database and every System Manager can read them.
```

Saved answers are ordinary memory notes; edit them in Desk or with `update_memory`. Third-person facts keep the notes descriptive.

## Privacy Policy

Everything ERPSmith stores lives in your site's own database. It has no telemetry and no hosted service. Tool results go to the model provider behind the MCP client you use, as with any tool; the only other network call is the public ERPNext manual fetch from docs.erpnext.com. Plans keep their arguments in plaintext as an audit trail and are pruned once they are more than 7 days old and can no longer unlock anything (a daily job, so the scheduler has to be running). DocType metadata is readable by any signed-in user through `get_doctype` and the debug tools, as it is through Frappe's own desk. Read [PRIVACY.md](PRIVACY.md) for the full policy.

## Development

```bash
cd dev && docker compose up -d
docker compose exec -T bench15 bash -lc "bash /workspace/erpsmith/dev/setup-bench.sh version-15 python3.12 test15.localhost 1 22"
docker compose exec -T bench16 bash -lc "bash /workspace/erpsmith/dev/setup-bench.sh version-16 python3.14 test16.localhost 2 24"
dev/test.sh                    # the suite on both benches
dev/fresh_install.sh bench15   # install on a throwaway site and check it (also bench16)
```

CI (`.github/workflows/ci.yml`) runs the suite on Frappe v15 and v16 for every push.

## Credit and licence

Written by Abhinav Prakash (abhinav.prakash1992@gmail.com). Free for everyone, released under the MIT licence (see `license.txt`). The embedding model is `sentence-transformers/all-MiniLM-L6-v2` under Apache-2.0 (see `erpsmith/embedding/NOTICE`).

Not affiliated with or endorsed by Frappe Technologies.
