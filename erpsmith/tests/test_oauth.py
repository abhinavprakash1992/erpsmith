import json
import time
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from werkzeug.exceptions import NotFound
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from erpsmith import oauth

CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"


class TestMetadata(FrappeTestCase):
	def test_protected_resource_points_at_this_site(self):
		m = oauth.protected_resource_metadata()
		self.assertEqual(m["authorization_servers"], [frappe.utils.get_url()])
		self.assertEqual(m["resource"], frappe.utils.get_url())

	def test_authorization_server_metadata(self):
		m = oauth.authorization_server_metadata()
		site = frappe.utils.get_url()
		self.assertEqual(m["issuer"], site)
		self.assertIn("S256", m["code_challenge_methods_supported"])
		self.assertEqual(m["authorization_endpoint"], f"{site}/api/method/frappe.integrations.oauth2.authorize")
		self.assertEqual(m["token_endpoint"], f"{site}/api/method/frappe.integrations.oauth2.get_token")
		self.assertEqual(m["registration_endpoint"], f"{site}/api/method/erpsmith.oauth.register_client")

	def test_renderer_serves_json_only_when_core_does_not(self):
		with patch.object(oauth, "CORE_OAUTH", False):
			r = oauth.WellKnownRenderer(".well-known/oauth-authorization-server")
			self.assertTrue(r.can_render())
			resp = r.render()
			self.assertEqual(resp.mimetype, "application/json")
			self.assertEqual(json.loads(resp.get_data())["issuer"], frappe.utils.get_url())
			prm = oauth.WellKnownRenderer(".well-known/oauth-protected-resource/api/method/erpsmith.mcp.handle")
			self.assertIn("authorization_servers", json.loads(prm.render().get_data()))
			self.assertFalse(oauth.WellKnownRenderer("about").can_render())
		with patch.object(oauth, "CORE_OAUTH", True):
			self.assertFalse(oauth.WellKnownRenderer(".well-known/oauth-authorization-server").can_render())


	def test_renderer_matches_whole_path_segments_only(self):
		with patch.object(oauth, "CORE_OAUTH", False):
			for path in (".well-known/oauth-authorization-server-x", ".well-known/oauth-protected-resources",
					".well-known/oauth-authorization-server.json"):
				self.assertFalse(oauth.WellKnownRenderer(path).can_render(), path)
			# BaseRenderer strips slashes, so the form Frappe hands over has no leading one
			self.assertTrue(oauth.WellKnownRenderer("/.well-known/oauth-authorization-server/").can_render())
			self.assertTrue(oauth.WellKnownRenderer(".well-known/oauth-authorization-server/tenant").can_render())

	def test_core_oauth_switch_tracks_the_frappe_version(self):
		# unpatched: v16 core serves discovery and registration, v15 does not
		self.assertEqual(oauth.CORE_OAUTH, int(frappe.__version__.split(".")[0]) >= 16)

	def test_only_auth_methods_the_v15_token_endpoint_can_serve_are_advertised(self):
		# Basic auth on /get_token is read by Frappe as API-key auth and answered with 401
		methods = oauth.authorization_server_metadata()["token_endpoint_auth_methods_supported"]
		self.assertEqual(sorted(methods), ["client_secret_post", "none"])


class TestRegistration(FrappeTestCase):
	def test_public_client_gets_no_secret(self):
		body = oauth.register({"redirect_uris": [CLAUDE_CALLBACK], "client_name": "Claude",
			"token_endpoint_auth_method": "none"})
		client = frappe.get_doc("OAuth Client", body["client_id"])
		self.assertEqual(client.redirect_uris, CLAUDE_CALLBACK)
		self.assertEqual(client.default_redirect_uri, CLAUDE_CALLBACK)
		self.assertEqual(client.grant_type, "Authorization Code")
		self.assertEqual(client.app_name, "Claude")
		self.assertNotIn("client_secret", body)
		self.assertEqual(body["token_endpoint_auth_method"], "none")

	def test_confidential_client_gets_its_secret(self):
		body = oauth.register({"redirect_uris": [CLAUDE_CALLBACK, "http://localhost:6274/oauth/callback"]})
		client = frappe.get_doc("OAuth Client", body["client_id"])
		self.assertEqual(body["client_secret"], client.client_secret)
		self.assertEqual(body["token_endpoint_auth_method"], "client_secret_post")
		self.assertEqual(client.redirect_uris.split(" "), body["redirect_uris"])

	def test_bad_redirect_uris_are_rejected(self):
		for uris in ([], "https://not-a-list", ["http://evil.example/cb"], ["javascript:alert(1)"],
				["https://ok.example/cb with space"], ["https://ok.example/cb#frag"], [123]):
			with self.assertRaises(oauth.RegistrationError, msg=repr(uris)):
				oauth.register({"redirect_uris": uris})

	def test_unknown_auth_method_is_rejected(self):
		with self.assertRaises(oauth.RegistrationError):
			oauth.register({"redirect_uris": [CLAUDE_CALLBACK], "token_endpoint_auth_method": "private_key_jwt"})

	def test_unsafe_redirect_uris_are_rejected(self):
		# characters Frappe would mangle on save or print into the consent page, and shapes that break parsing
		for uri in ('https://ok.example/cb"x', "https://ok.example/cb<x>", "https://ok.example/cb`x",
				"https://ok.example/c\\b", "https://ok.example/cb\nx", "https://ok.example/cb\tx", "https://[bad/cb",
				"https://ok.example/" + "a" * 130, "https://", "//ok.example/cb", "ftp://ok.example/cb", "http://localhost.evil.example/cb",
				"http://127.0.0.1.evil.example/cb", "https://ok.example/cb#"):
			with self.assertRaises(oauth.InvalidRedirectURI, msg=repr(uri)):
				oauth.register({"redirect_uris": [uri]})
		with self.assertRaises(oauth.InvalidRedirectURI):
			oauth.register({"redirect_uris": [f"https://ok.example/{i}" for i in range(oauth.MAX_REDIRECT_URIS + 1)]})

	def test_loopback_and_query_redirect_uris_are_accepted(self):
		uris = ["http://localhost:6274/oauth/callback", "http://127.0.0.1:33418/cb", "https://ok.example/cb?a=b&c=d%20e",
			"https://ok.example/" + "a" * 100]
		self.assertEqual(oauth.register({"redirect_uris": uris})["redirect_uris"], uris)

	def test_client_name_cannot_inject_markup_into_the_consent_page(self):
		# the consent page prints OAuth Client.app_name unescaped; Frappe's sanitizer still lets <a> and styled <div> through
		name = '<a href="https://evil.example">Claude</a><div style="position:fixed">Sign in</div>'
		body = oauth.register({"redirect_uris": [CLAUDE_CALLBACK], "client_name": name})
		stored = frappe.db.get_value("OAuth Client", body["client_id"], "app_name")
		for text in (stored, body["client_name"]):
			self.assertFalse(set("<>&\"'`") & set(text), text)
			self.assertIn("Claude", text)

	def test_client_name_defaults_when_missing_or_not_text(self):
		for payload in ({}, {"client_name": ""}, {"client_name": "<>"}, {"client_name": ["x"]}, {"client_name": 7}):
			body = oauth.register({"redirect_uris": [CLAUDE_CALLBACK], **payload})
			self.assertEqual(body["client_name"], "MCP client", repr(payload))
		long = oauth.register({"redirect_uris": [CLAUDE_CALLBACK], "client_name": "n" * 500})["client_name"]
		self.assertEqual(len(long), 140)

	def test_client_secret_basic_is_registered_as_post(self):
		body = oauth.register({"redirect_uris": [CLAUDE_CALLBACK], "token_endpoint_auth_method": "client_secret_basic"})
		self.assertEqual(body["token_endpoint_auth_method"], "client_secret_post")
		self.assertIn("client_secret", body)

	def test_client_is_created_with_consent_and_the_default_role_gate_intact(self):
		client = frappe.get_doc("OAuth Client", oauth.register({"redirect_uris": [CLAUDE_CALLBACK]})["client_id"])
		self.assertEqual((client.skip_authorization, client.response_type, client.scopes), (0, "Code", "all openid"))
		self.assertEqual([r.role for r in client.allowed_roles], ["Desk User"])

	def test_issued_at_is_the_current_epoch_time(self):
		# system time zone is not UTC on some sites; creation is a naive local datetime
		issued = oauth.register({"redirect_uris": [CLAUDE_CALLBACK]})["client_id_issued_at"]
		self.assertLess(abs(issued - time.time()), 5)


class TestRegisterClientEndpoint(FrappeTestCase):
	IP = "203.0.113.77"

	def setUp(self):
		self.saved = {k: getattr(frappe.local, k, None) for k in ("request", "request_ip", "form_dict")}
		frappe.local.request_ip = self.IP
		self.clear_limit()

	def tearDown(self):
		self.clear_limit()
		for k, v in self.saved.items():
			setattr(frappe.local, k, v)
		frappe.set_user("Administrator")

	def clear_limit(self):
		frappe.cache.delete_keys("rl:erpsmith.oauth.register_client:" + self.IP)

	def post(self, body):
		env = EnvironBuilder(method="POST", path="/api/method/erpsmith.oauth.register_client", data=body,
			content_type="application/json").get_environ()
		frappe.local.request = Request(env)
		frappe.set_user("Guest")  # resets form_dict, so set cmd (the rate limit key) afterwards
		frappe.local.form_dict = frappe._dict(cmd="erpsmith.oauth.register_client")
		return oauth.register_client()

	def test_guest_registers_a_client(self):
		with patch.object(oauth, "CORE_OAUTH", False):
			r = self.post(json.dumps({"redirect_uris": [CLAUDE_CALLBACK], "client_name": "Claude",
				"token_endpoint_auth_method": "none"}))
		self.assertEqual(r.status_code, 201)
		self.assertEqual((r.mimetype, r.headers["Cache-Control"]), ("application/json", "no-store"))
		body = json.loads(r.get_data())
		self.assertNotIn("client_secret", body)
		self.assertTrue(frappe.db.exists("OAuth Client", body["client_id"]))

	def test_bad_requests_get_a_400_error_document_and_create_nothing(self):
		before = frappe.db.count("OAuth Client")
		with patch.object(oauth, "CORE_OAUTH", False):
			for raw, code in (("{not json", "invalid_client_metadata"), ("[1]", "invalid_client_metadata"),
					("", "invalid_redirect_uri"), (json.dumps({"redirect_uris": ["http://evil.example/cb"]}), "invalid_redirect_uri"),
					(json.dumps({"redirect_uris": [CLAUDE_CALLBACK], "token_endpoint_auth_method": "private_key_jwt"}),
						"invalid_client_metadata")):
				r = self.post(raw)
				self.assertEqual((r.status_code, json.loads(r.get_data())["error"]), (400, code), raw)
		self.assertEqual(frappe.db.count("OAuth Client"), before)

	def test_registration_is_rate_limited_per_ip(self):
		with patch.object(oauth, "CORE_OAUTH", False):
			for _ in range(20):
				self.assertEqual(self.post("[]").status_code, 400)
			with self.assertRaises(frappe.RateLimitExceededError):
				self.post("[]")

	def test_inert_when_core_serves_registration(self):
		# v16: core's endpoint (and its OAuth Settings switch) is the only way to register
		before = frappe.db.count("OAuth Client")
		with patch.object(oauth, "CORE_OAUTH", True), self.assertRaises(NotFound):
			self.post(json.dumps({"redirect_uris": [CLAUDE_CALLBACK]}))
		self.assertEqual(frappe.db.count("OAuth Client"), before)
