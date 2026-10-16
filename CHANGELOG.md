# Changelog

## 1.0.0

First release. ERPSmith lets Claude (or any MCP client) build and debug customizations on your ERPNext site, and nothing changes until you approve it. Frappe v15 and v16. Free, MIT.

- **59 tools** at one MCP endpoint on your site: read DocTypes, records, fields, permissions, workflows, scripts and error logs; create and change DocTypes, fields, server and client scripts, workflows, notifications, print formats, reports and dashboard charts; debug permissions, data integrity, broken links and stuck documents.
- **Plan gate.** Every write waits for a plan the user has seen and approved. Approved plans unlock exactly the changes they list, are recorded as an audit trail, and are pruned when expired or used.
- **Sign in with your own account (OAuth)** on Frappe v15 and v16, so every tool call runs with that user's permissions. No API keys to hand out.
- **Memory.** Notes the assistant saves (rules, patterns, fixes) are searched by meaning, shared by everyone on the site, and survive upgrades. A starter set of notes is installed with the app.
- **Site knowledge.** An index of your own site (custom fields, workflows, scripts, print formats, company and stock settings) and, optionally, the ERPNext manual, built in the background and kept fresh when those records change.
- **Safe by default.** Dangerous operations (changing User roles, passwords or API keys, bulk deletes) are blocked, server scripts are checked before they are saved, and database errors reach the model as a short message plus an Error Log name, never as raw SQL.
- Runs inside your site: no hosted service, no licence key, no data sent to a third party.
