#!/usr/bin/env python3
"""OAuth + MCP smoke test against a dev bench (stdlib only).

    eval "$(dev/admin-keys.sh 15)"; python3 dev/oauth_smoke.py http://localhost:8015

Walks the path Claude takes: 401 → protected-resource metadata → authorization-server
metadata → dynamic registration (public client) → login → authorize with PKCE S256 →
token → MCP calls with the bearer token. Also checks that a wrong PKCE verifier is refused.
Dev-only shortcut: it marks its own client skip_authorization (via the admin API key)
so no consent click is needed; the consent screen is Frappe core and gets exercised by
hand in the Claude test.
"""

import base64
import hashlib
import http.cookiejar
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1].rstrip("/")
MCP = f"{BASE}/api/method/erpsmith.mcp.handle"
# Claude's real callback when testing over https (v16 core refuses http redirect URIs on an https site); the MCP inspector's otherwise.
REDIRECT = "https://claude.ai/api/mcp/auth_callback" if BASE.startswith("https://") else "http://localhost:6274/oauth/callback"
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
	def redirect_request(self, *args, **kwargs):
		return None


jar = http.cookiejar.CookieJar()
browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect)  # has the login session
client = urllib.request.build_opener(NoRedirect)  # no cookies: token and MCP calls must not ride on the session


def req(method, url, data=None, headers=None, form=False, opener=client):
	headers = dict(headers or {})
	body = None
	if data is not None:
		if form:
			body = urllib.parse.urlencode(data).encode()
			headers["Content-Type"] = "application/x-www-form-urlencoded"
		else:
			body = json.dumps(data).encode()
			headers["Content-Type"] = "application/json"
	try:
		resp = opener.open(urllib.request.Request(url, data=body, headers=headers, method=method))
	except urllib.error.HTTPError as e:
		resp = e
	return resp.getcode(), dict(resp.headers), resp.read().decode()


def check(label, ok, detail=""):
	print(("PASS  " if ok else "FAIL  ") + label)
	if not ok:
		print("      " + str(detail)[:800])
		sys.exit(1)


status, headers, _ = req("POST", MCP, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, MCP_HEADERS)
check("MCP call without a token → 401", status == 401, status)
www = headers.get("WWW-Authenticate", "")
check("401 carries resource_metadata", 'resource_metadata="' in www, www)
prm_url = www.split('resource_metadata="')[1].split('"')[0]

status, _, body = req("GET", prm_url)
check("GET protected-resource metadata (risk 1 on v15)", status == 200, f"{status} {body}")
issuer = json.loads(body)["authorization_servers"][0]
status, _, body = req("GET", f"{issuer}/.well-known/oauth-authorization-server")
check("GET authorization-server metadata (risk 1 on v15)", status == 200, f"{status} {body}")
meta = json.loads(body)
check("PKCE S256 advertised", "S256" in meta.get("code_challenge_methods_supported", []), body)

status, _, body = req("POST", meta["registration_endpoint"], {
	"redirect_uris": [REDIRECT], "client_name": "oauth-smoke", "token_endpoint_auth_method": "none",
	"grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]})
check("dynamic client registration → 201", status == 201, f"{status} {body}")
client_id = json.loads(body)["client_id"]
# Ask for the scope the server registered (v16 core: "all"; v15 shim: "all openid"). NO_SCOPE=1 omits the
# parameter, as a client does when the server advertises no scopes_supported.
SCOPE = None if os.environ.get("NO_SCOPE") else json.loads(body).get("scope", "all openid")
SCOPE_PARAMS = {} if SCOPE is None else {"scope": SCOPE}

status, _, body = req("PUT", f"{BASE}/api/resource/OAuth%20Client/{client_id}", {"skip_authorization": 1},
	{"Authorization": f"token {os.environ['API_KEY']}:{os.environ['API_SECRET']}"})
check("dev shortcut: skip consent for the smoke client", status == 200, f"{status} {body}")

status, _, body = req("POST", f"{BASE}/api/method/login", {"usr": "Administrator", "pwd": os.environ.get("ADMIN_PWD", "admin")}, opener=browser)
check("log in as the dev Administrator", status == 200, f"{status} {body}")


def pkce():
	verifier = secrets.token_urlsafe(48)
	challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
	return verifier, challenge


def authorization_code(challenge):
	query = urllib.parse.urlencode({"response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
		**SCOPE_PARAMS, "state": "smoke", "code_challenge": challenge, "code_challenge_method": "S256"})
	status, headers, body = req("GET", f"{meta['authorization_endpoint']}?{query}", opener=browser)
	location = headers.get("Location", "")
	check("authorize redirects back with a code", status in (301, 302, 303) and location.startswith(REDIRECT)
		and "code=" in location, f"{status} {location} {body}")
	return urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["code"][0]


def exchange(code, verifier):
	return req("POST", meta["token_endpoint"], {"grant_type": "authorization_code", "code": code,
		"redirect_uri": REDIRECT, "client_id": client_id, "code_verifier": verifier}, form=True)


verifier, challenge = pkce()
status, _, body = exchange(authorization_code(challenge), "wrong-" + verifier)
check("wrong PKCE verifier is refused (risk 2)", status >= 400 and "access_token" not in body, f"{status} {body}")
verifier, challenge = pkce()
status, _, body = exchange(authorization_code(challenge), verifier)
check("public client + PKCE gets an access token (risk 2)", status == 200 and "access_token" in body, f"{status} {body}")
token = json.loads(body)
bearer = {**MCP_HEADERS, "Authorization": f"Bearer {token['access_token']}"}

status, _, body = req("POST", MCP, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
	"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "oauth-smoke", "version": "0"}}}, bearer)
check("MCP initialize with the bearer token", status == 200 and json.loads(body)["result"]["serverInfo"]["name"] == "erpsmith", f"{status} {body}")
status, _, body = req("POST", MCP, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, bearer)
check("tools/list includes get_record", "get_record" in [t["name"] for t in json.loads(body)["result"]["tools"]], body)
status, _, body = req("POST", MCP, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
	"params": {"name": "get_current_plan", "arguments": {}}}, bearer)
check("tools/call runs as the token's user", status == 200 and not json.loads(body)["result"].get("isError"), body)

# The walking skeleton the user repeats through Claude: a write is refused until its plan is approved, runs once, cannot be replayed.
def tool(name, arguments, id_):
	status, _, body = req("POST", MCP, {"jsonrpc": "2.0", "id": id_, "method": "tools/call",
		"params": {"name": name, "arguments": arguments}}, bearer)
	result = json.loads(body)["result"]
	return bool(result.get("isError")), json.loads(result["content"][0]["text"])


TODO = {"doctype": "ToDo", "data": {"description": "erpsmith-oauth-smoke"}}
is_err, out = tool("submit_plan", {"summary": "smoke: create one ToDo", "changes": [
	{"tool": "create_record", "args": TODO, "reason": "oauth smoke"}]}, 20)
check("submit_plan returns a planId", not is_err and out.get("planId"), out)
plan_id = out["planId"]
is_err, out = tool("create_record", TODO, 21)
check("write is refused before the plan is approved", is_err and "BLOCKED by plan gate" in out.get("message", ""), out)
is_err, out = tool("approve_plan", {"planId": plan_id}, 22)
check("approve_plan", not is_err and out.get("approved") is True, out)
is_err, out = tool("create_record", TODO, 23)
check("approved write runs", not is_err and out.get("doctype") == "ToDo" and out.get("name"), out)
todo_name = out["name"]
is_err, out = tool("create_record", TODO, 24)
check("replaying the approved write is refused", is_err and "BLOCKED by plan gate" in out.get("message", ""), out)
req("DELETE", f"{BASE}/api/resource/ToDo/{todo_name}", None, {"Authorization": f"token {os.environ['API_KEY']}:{os.environ['API_SECRET']}"})

# Claude relies on refresh once the (default 1 hour) access token expires.
check("token response includes a refresh_token", bool(token.get("refresh_token")), json.dumps(token)[:300])
status, _, body = req("POST", meta["token_endpoint"], {"grant_type": "refresh_token",
	"refresh_token": token["refresh_token"], "client_id": client_id}, form=True)
check("refresh_token grant for the public client", status == 200 and "access_token" in body, f"{status} {body}")
refreshed = json.loads(body)
bearer2 = {**MCP_HEADERS, "Authorization": f"Bearer {refreshed['access_token']}"}
status, _, body = req("POST", MCP, {"jsonrpc": "2.0", "id": 4, "method": "ping"}, bearer2)
check("refreshed access token works on the MCP endpoint", status == 200 and json.loads(body).get("result") == {}, f"{status} {body}")

# A revoked/expired token must get the same 401 + WWW-Authenticate as no token, so Claude re-runs OAuth.
status, _, body = req("POST", meta["revocation_endpoint"], {"token": refreshed["access_token"], "client_id": client_id}, form=True)
check("revoke the access token", status == 200, f"{status} {body}")
status, headers, body = req("POST", MCP, {"jsonrpc": "2.0", "id": 5, "method": "ping"}, bearer2)
check("revoked token → 401", status == 401, f"{status} {body[:300]}")
check("revoked token 401 carries resource_metadata", 'resource_metadata="' in headers.get("WWW-Authenticate", ""), dict(headers))
status, headers, body = req("POST", MCP, {"jsonrpc": "2.0", "id": 6, "method": "ping"}, {**MCP_HEADERS, "Authorization": "Bearer not-a-token"})
check("garbage token → 401", status == 401, f"{status} {body[:300]}")
check("garbage token 401 carries resource_metadata", 'resource_metadata="' in headers.get("WWW-Authenticate", ""), dict(headers))
print("ALL PASS")
