# Demo video script (about 90 seconds)

Use a throwaway demo site ("ERPSmith Demo Co", synthetic data) with the app installed and Claude connected as a dedicated AI user. Screen capture with voice-over; no site or customer data on screen.

| Time | On screen | Voice-over |
|---|---|---|
| 0:00-0:08 | Title card: ERPSmith, "AI dev copilot for ERPNext". | "ERPSmith lets Claude build and debug customizations on your ERPNext site, and you approve every change to your site." |
| 0:08-0:20 | Claude: Add custom connector, paste the site's MCP address (`https://<site>/api/method/erpsmith.mcp.handle`), sign-in page on the site, Approve. | "Add your site as a custom connector. You enter only the MCP address, sign in on your own site, and Claude acts as that user." |
| 0:20-0:35 | Prompt: "Add a Data field called Preferred Contact Time to Customer." Claude shows a plan with one change. | "Anything that writes starts as a plan. This one adds one field. Nothing has changed yet." |
| 0:35-0:45 | Approve the plan; Claude runs it; open a Customer form with the new field. | "I approve it, it runs, and the field is there." |
| 0:45-0:55 | Desk: MCP Plan list showing the plan and its change. | "Plans stay in Desk as an audit trail until they are more than 7 days old and can no longer unlock anything." |
| 0:55-1:10 | Prompt: "Write a server script that sets the territory before save." Claude reports a sandbox problem (an import) and fixes it using a memory note. | "ERPSmith knows the server-script sandbox: imports fail there. It finds the fix in its memory before saving." |
| 1:10-1:20 | Prompt: "Run a full health check." Result summary. | "Debug tools check permissions, broken links, stuck documents and error logs." |
| 1:20-1:30 | End card: GitHub address, "Free, MIT, Frappe v15 and v16", "Not affiliated with or endorsed by Frappe Technologies". | "ERPSmith is free and open source. Link below." |

Before recording: run the prompts once off camera, clear the plan list, and make sure the site shows no real names or emails.
