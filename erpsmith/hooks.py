app_name = "erpsmith"
app_title = "ERPSmith"
app_publisher = "Abhinav Prakash"
app_description = "AI dev copilot for ERPNext"
app_email = "abhinav.prakash1992@gmail.com"
app_license = "mit"

# v15 only: serve /.well-known/oauth-* (v16 core handles these before the website router)
page_renderer = ["erpsmith.oauth.WellKnownRenderer"]
