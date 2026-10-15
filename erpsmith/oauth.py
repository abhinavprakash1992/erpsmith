"""OAuth discovery (RFC 8414 / RFC 9728) and dynamic client registration (RFC 7591) for Frappe v15.

v16 core serves all of this itself (frappe.integrations.oauth2.handle_wellknown and
register_client, enabled by default in OAuth Settings); there CORE_OAUTH is True and
this module stays out of the way. The authorize and token endpoints, PKCE and bearer
validation are Frappe core on both versions.
"""

import json
import re
import time
from urllib.parse import urlparse

import frappe
import frappe.integrations.oauth2
from frappe.rate_limiter import rate_limit
from frappe.website.page_renderers.base_renderer import BaseRenderer
from werkzeug.exceptions import NotFound
from werkzeug.wrappers import Response

CORE_OAUTH = hasattr(frappe.integrations.oauth2, "handle_wellknown")
PROTECTED_RESOURCE = ".well-known/oauth-protected-resource"
AUTH_SERVER = ".well-known/oauth-authorization-server"
SCOPES = ["all", "openid"]
# v15's token endpoint cannot read client_secret_basic: Frappe treats an Authorization: Basic
# header as API-key auth and answers 401. It does not check the secret at all, so only
# the two methods that work are advertised.
AUTH_METHODS = ["client_secret_post", "none"]
CORS = {"Access-Control-Allow-Origin": "*"}
MAX_REDIRECT_URIS = 10
MAX_URI_LENGTH = 140  # OAuth Client.default_redirect_uri is a Data field
# RFC 3986 characters minus "#": no quotes, <>, spaces or control characters
URI_CHARS = re.compile(r"[A-Za-z0-9\-._~:/?\[\]@!$&'()*+,;=%]+")
NAME_UNSAFE = re.compile(r"[<>&\"'`\x00-\x1f\x7f]")  # the consent page prints the name unescaped


def protected_resource_metadata():
	site = frappe.utils.get_url()
	return {
		"resource": site,
		"authorization_servers": [site],
		"bearer_methods_supported": ["header"],
		"scopes_supported": SCOPES,
		"resource_name": "ERPSmith",
	}


def authorization_server_metadata():
	site = frappe.utils.get_url()

	def method(path):
		return f"{site}/api/method/{path}"

	return {
		"issuer": site,
		"authorization_endpoint": method("frappe.integrations.oauth2.authorize"),
		"token_endpoint": method("frappe.integrations.oauth2.get_token"),
		"revocation_endpoint": method("frappe.integrations.oauth2.revoke_token"),
		"userinfo_endpoint": method("frappe.integrations.oauth2.openid_profile"),
		"registration_endpoint": method("erpsmith.oauth.register_client"),
		"response_types_supported": ["code"],
		"grant_types_supported": ["authorization_code", "refresh_token"],
		"code_challenge_methods_supported": ["S256"],
		"token_endpoint_auth_methods_supported": AUTH_METHODS,
		"scopes_supported": SCOPES,
	}


def _under(path, prefix):
	"""The well-known path itself, or the RFC 8414/9728 form with the issuer/resource path appended."""
	return path == prefix or path.startswith(prefix + "/")


class WellKnownRenderer(BaseRenderer):
	"""Website renderer (hooks.page_renderer) for the two discovery documents on v15.

	Frappe builds one of these for every website request, so can_render stays cheap.
	BaseRenderer strips the leading slash from the path.
	"""

	def can_render(self):
		return not CORE_OAUTH and (_under(self.path, PROTECTED_RESOURCE) or _under(self.path, AUTH_SERVER))

	def render(self):
		body = protected_resource_metadata() if _under(self.path, PROTECTED_RESOURCE) else authorization_server_metadata()
		return Response(json.dumps(body), mimetype="application/json", headers=CORS)


class RegistrationError(Exception):
	code = "invalid_client_metadata"


class InvalidRedirectURI(RegistrationError):
	code = "invalid_redirect_uri"


def _redirect_ok(uri):
	if not isinstance(uri, str) or len(uri) > MAX_URI_LENGTH or not URI_CHARS.fullmatch(uri):
		return False
	try:
		u = urlparse(uri)
	except ValueError:  # e.g. an unterminated IPv6 literal
		return False
	if not u.netloc:
		return False
	return u.scheme == "https" or (u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1"))


def _client_name(value):
	name = " ".join(NAME_UNSAFE.sub("", value).split()) if isinstance(value, str) else ""
	return name[:140] or "MCP client"


def register(payload):
	uris = payload.get("redirect_uris")
	if not isinstance(uris, list) or not 0 < len(uris) <= MAX_REDIRECT_URIS:
		raise InvalidRedirectURI(f"redirect_uris must be a list of 1 to {MAX_REDIRECT_URIS} URLs")
	if any(not _redirect_ok(u) for u in uris):
		raise InvalidRedirectURI(
			"Only https:// or http://localhost redirect URIs of up to "
			f"{MAX_URI_LENGTH} characters, without fragments or unsafe characters, are allowed"
		)
	auth_method = payload.get("token_endpoint_auth_method") or "client_secret_post"
	if auth_method == "client_secret_basic":
		auth_method = "client_secret_post"  # RFC 7591 3.2.1: the server may substitute a method it supports
	if auth_method not in AUTH_METHODS:
		raise RegistrationError("Unsupported token_endpoint_auth_method")
	client = frappe.get_doc({
		"doctype": "OAuth Client",
		"app_name": _client_name(payload.get("client_name")),
		"redirect_uris": " ".join(uris),  # Frappe's redirect URI delimiter is a space
		"default_redirect_uri": uris[0],
		"grant_type": "Authorization Code",
		"response_type": "Code",
		"scopes": " ".join(SCOPES),
		"skip_authorization": 0,
	}).insert(ignore_permissions=True)
	body = {
		"client_id": client.client_id,
		"client_name": client.app_name,
		"redirect_uris": uris,
		"grant_types": ["authorization_code", "refresh_token"],
		"response_types": ["code"],
		"token_endpoint_auth_method": auth_method,
		"scope": " ".join(SCOPES),
		"client_id_issued_at": int(time.time()),
	}
	if auth_method != "none":
		body["client_secret"] = client.client_secret
		body["client_secret_expires_at"] = 0
	return body


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=20, seconds=60 * 60)
def register_client():
	"""Unauthenticated by design (RFC 7591): it only creates a client that still needs a signed-in user's consent."""
	if CORE_OAUTH:
		raise NotFound  # v16 core registers clients, and honours OAuth Settings > dynamic client registration
	try:
		payload = json.loads(frappe.request.get_data(as_text=True) or "{}")
	except ValueError:
		payload = None
	try:
		if not isinstance(payload, dict):
			raise RegistrationError("Request body must be a JSON object")
		body, status = register(payload), 201
	except RegistrationError as e:
		body, status = {"error": e.code, "error_description": str(e)}, 400
	return Response(json.dumps(body), status=status, mimetype="application/json",
		headers={**CORS, "Cache-Control": "no-store"})
