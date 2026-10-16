app_name = "erpsmith"
app_title = "ERPSmith"
app_publisher = "Abhinav Prakash"
app_description = "AI dev copilot for ERPNext"
app_email = "abhinav.prakash1992@gmail.com"
app_license = "mit"

# v15 only: serve /.well-known/oauth-* (v16 core handles these before the website router)
page_renderer = ["erpsmith.oauth.WellKnownRenderer"]

# A 401 for a bad, expired or revoked bearer token never reaches the endpoint, so it gets its challenge here
# (v15 core adds none; v16 core adds one itself and replaces ours)
after_request = ["erpsmith.mcp.add_www_authenticate"]

# Put the seed notes in place (safe to repeat); a failure is logged, never raised
after_install = "erpsmith.install.after_install"
after_migrate = ["erpsmith.install.after_migrate"]

# Refresh the knowledge index when a record it is built from changes (the list is knowledge.REFRESH_DOCTYPES)
doc_events = {
	"Workflow": {"on_update": "erpsmith.knowledge.on_source_change", "on_trash": "erpsmith.knowledge.on_source_change"},
	"Server Script": {"on_update": "erpsmith.knowledge.on_source_change", "on_trash": "erpsmith.knowledge.on_source_change"},
	"Client Script": {"on_update": "erpsmith.knowledge.on_source_change", "on_trash": "erpsmith.knowledge.on_source_change"},
	"Notification": {"on_update": "erpsmith.knowledge.on_source_change", "on_trash": "erpsmith.knowledge.on_source_change"},
	"Print Format": {"on_update": "erpsmith.knowledge.on_source_change", "on_trash": "erpsmith.knowledge.on_source_change"},
	"Custom Field": {"on_update": "erpsmith.knowledge.on_source_change", "on_trash": "erpsmith.knowledge.on_source_change"},
	"Property Setter": {"on_update": "erpsmith.knowledge.on_source_change", "on_trash": "erpsmith.knowledge.on_source_change"},
	"DocType": {"on_update": "erpsmith.knowledge.on_source_change", "on_trash": "erpsmith.knowledge.on_source_change"},
	"Company": {"on_update": "erpsmith.knowledge.on_source_change", "on_trash": "erpsmith.knowledge.on_source_change"},
	"Warehouse": {"on_update": "erpsmith.knowledge.on_source_change", "on_trash": "erpsmith.knowledge.on_source_change"},
	"Currency": {"on_update": "erpsmith.knowledge.on_source_change", "on_trash": "erpsmith.knowledge.on_source_change"},
	"Fiscal Year": {"on_update": "erpsmith.knowledge.on_source_change", "on_trash": "erpsmith.knowledge.on_source_change"},
	"Global Defaults": {"on_update": "erpsmith.knowledge.on_source_change", "on_trash": "erpsmith.knowledge.on_source_change"},
	"Price List": {"on_update": "erpsmith.knowledge.on_source_change", "on_trash": "erpsmith.knowledge.on_source_change"},
	"Stock Settings": {"on_update": "erpsmith.knowledge.on_source_change", "on_trash": "erpsmith.knowledge.on_source_change"},
}
