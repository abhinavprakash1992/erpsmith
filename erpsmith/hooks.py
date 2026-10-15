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
