# Forum announcement (discuss.frappe.io, category: Show and Tell or Share a Hack)

**Title:** ERPSmith: an AI dev copilot for ERPNext (free, MIT, Frappe v15 and v16)

Hi all,

I have released ERPSmith, a Frappe app that lets Claude (or any MCP client) build and debug customizations on your site while you approve every change to your site's records and customizations.

**What it does**
- 59 tools: DocTypes and fields, client and server scripts, workflows, permissions, print formats, reports, notifications, dashboard charts, and debug and fix tools for permissions, data integrity, broken links and stuck documents.
- A plan gate: every change to your site's records and customizations waits for a plan you have read and approved (memory notes are the one thing not gated). Plans are listed in Desk as an audit trail, deleted once they are more than 7 days old and can no longer unlock anything.
- Everything runs as the signed-in user, so roles and permissions apply. Sign-in is OAuth on v15 and v16; you add `https://<your-site>/api/method/erpsmith.mcp.handle` in Claude as a custom connector and enter nothing else.
- Memory: 96 curated notes on the server-script sandbox (what is blocked, what to use instead), patterns and fixes, searchable by meaning. It also indexes your site's own customizations and keeps a generated site profile.

**Privacy.** Everything stays in your site's database. No telemetry, no hosted service. The one outside request is the public manual from docs.erpnext.com. Tool results go to the model provider of the client you use. Full policy in PRIVACY.md.

**Install**
```
bench get-app https://github.com/abhinavprakash1992/erpsmith
bench --site your-site install-app erpsmith
bench --site your-site migrate
```
Server script tools need server scripts enabled (a private bench on Frappe Cloud).

It is free for everyone (MIT). Source, README and issues: https://github.com/abhinavprakash1992/erpsmith

I would like to hear what breaks on your sites, especially on v16 and on sites with many custom scripts.

Abhinav Prakash

Not affiliated with or endorsed by Frappe Technologies.
