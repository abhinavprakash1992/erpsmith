# ERPSmith privacy policy

ERPSmith is a Frappe app that runs inside your own site. The author runs no server for it, receives no data from it and has no way to see your site. This policy describes what the app itself stores and what leaves your site.

Not affiliated with or endorsed by Frappe Technologies.

## Data collected

ERPSmith writes these records to your site while you use it:

- **Plans** (`MCP Plan`, `MCP Plan Change`): the summary of each plan, who made and approved it, and every proposed change with its tool name, arguments and reason. The arguments are stored in plaintext (not encrypted), because they are the audit trail of what was changed. If a change carries a value, for example a script body or a field value, that value is in the plan.
- **Memory notes** (`MCP Memory`): the seed notes shipped with the app, and the notes you or the assistant add: rules, patterns, fixes and company context.
- **Knowledge index** (`MCP Knowledge Chunk`): short chunks describing your DocTypes (custom fields included), workflows, scripts, client scripts, notifications and print formats, plus the ERPNext manual. Script code in the index has secrets scrubbed on a best-effort basis. Notification message bodies, literal recipient addresses and print format HTML are left out.
- **Site profile notes**: generated `context` notes on your companies, currencies, warehouses, naming series, installed apps, workflows and customization footprint.
- Standard Frappe logs: tool errors are written to the Error Log like any other server error.

ERPSmith collects no analytics, usage statistics, crash reports or identifiers. It has no telemetry.

## How it is used and stored

All of the above is stored in your site's own MariaDB database, in the same place as the rest of your data, and is covered by your own backups. The knowledge build runs in your site's own background worker. The data is used for three things: to let the assistant search notes and the index, to enforce the plan gate, and to give you an audit trail for as long as plans are retained (see Retention).

Who can read it: users with the System Manager role (the DocTypes grant no other role access). Tools run as the signed-in user, so every tool call is limited by that user's own permissions and a user without the System Manager role gets a permission error from the memory and knowledge tools. A user can read their own plans through `get_current_plan`.

One exception worth knowing: DocType metadata (field names, types, options and permission rules) is readable by any signed-in user through `get_doctype` and the debug tools. That matches Frappe's own `frappe.desk.form.load.getdoctype`, which serves the same metadata to every desk user.

## Third-party sharing

ERPSmith itself sends nothing to the author or any third party; your MCP client sends tool results to its model provider. What leaves your site:

- **Tool results go to the model provider of the MCP client you use.** When Claude (or another client) calls a tool, the result, which may include records, scripts, notes and plan contents, is sent to that provider under the terms of your agreement with them. Choose a user whose roles expose only what you are willing to send, and keep passwords, bank details and personal data out of notes and scripts.
- **The manual fetch.** To build the knowledge index, the site downloads the public ERPNext user manual from docs.erpnext.com, with an `ERPSmith/<version>` User-Agent. The docs server sees your site's IP address and that User-Agent; nothing else from your site is sent in that request.

There is no other network access, and no advertising, analytics or data sale.

## Retention

- Plans, with their changes, are deleted automatically once they are more than 7 days old and can no longer unlock anything (every change used, the plan expired, or never approved). A daily scheduled job does this, so it needs the scheduler running; without it plans stay.
- Memory notes, the knowledge index and the site profile stay until you delete them, rebuild the index or uninstall the app. Edits to the generated site-profile notes are overwritten on the next knowledge build, and a deleted profile note comes back, because the notes mirror your site's configuration.
- Uninstalling the app from a site removes its DocTypes and the data in them.

## This website

The ERPSmith website is a set of static pages. It sets no cookies and runs no analytics or tracking scripts. Its host, Vercel, keeps standard request logs (such as IP address, time and page requested) to serve and protect the site, under Vercel's own privacy policy.

## Contact

Abhinav Prakash, abhinav.prakash1992@gmail.com. Questions, data requests and security reports go to that address, or to the issues page at https://github.com/abhinavprakash1992/erpsmith.
