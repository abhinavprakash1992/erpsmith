# Frappe Cloud Marketplace listing

## Name

ERPSmith

## Short description

AI dev copilot for ERPNext: build and debug customizations

## Long description

ERPSmith lets Claude, or any MCP client, build and debug customizations on your ERPNext site while you stay in control. Free and open source (MIT).

**What it does.** 59 tools to read and change DocTypes and fields, client and server scripts, workflows, permissions, print formats, reports, notifications and dashboard charts, plus debug tools for permissions, data integrity, broken links, stuck documents and error logs.

**You approve every change.** Every change to your site's records and customizations waits for a plan you have read and approved; only memory notes are not gated. Plans are kept in Desk as an audit trail and are deleted once they are more than 7 days old and can no longer unlock anything. Everything runs as the signed-in user, so it can do only what that user's roles allow, and a few dangerous operations are blocked outright.

**It remembers.** 96 curated notes on Frappe's script sandbox, working patterns and known fixes come with the app, searchable by meaning. It also indexes your site's own customizations and writes a short site profile, and you can teach it how your company works.

**Sign in, nothing else.** Add `https://<your-site>/api/method/erpsmith.mcp.handle` as a custom connector in Claude and sign in with OAuth on Frappe v15 or v16. No keys to hand out.

**Private by design.** Everything stays in your site's database. No telemetry, no hosted service. The only outside request is the public manual from docs.erpnext.com. Tool results go to the model provider of the client you choose. Privacy policy: https://erpsmith.vercel.app/privacy

Server script tools need server scripts enabled (a private bench on Frappe Cloud).

Compatible with Frappe v15 and v16. Author: Abhinav Prakash.

Not affiliated with or endorsed by Frappe Technologies.

## Links

- Website: https://erpsmith.vercel.app
- Documentation: https://erpsmith.vercel.app/docs
- Source and issues: https://github.com/abhinavprakash1992/erpsmith
- Privacy policy: https://erpsmith.vercel.app/privacy
- Terms of use: https://erpsmith.vercel.app/terms
- Licence: MIT
