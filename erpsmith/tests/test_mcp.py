import json
import os
import re
import secrets
import threading
from unittest.mock import patch

import frappe
import frappe.app
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime
from werkzeug.test import Client, EnvironBuilder
from werkzeug.wrappers import Request, Response

from erpsmith import mcp, oauth

MCP_PATH = "/api/method/erpsmith.mcp.handle"
CHALLENGE = r'^Bearer resource_metadata="https?://[^"]+/\.well-known/oauth-protected-resource"$'


def schema(name):
	return {"name": name, "title": name.replace("_", " ").title(), "description": "test only",
		"inputSchema": {"type": "object", "properties": {}}}


def rpc(method, params=None, id=1):
	msg = {"jsonrpc": "2.0", "id": id, "method": method}
	if params is not None:
		msg["params"] = params
	return mcp.dispatch(msg)


def call(name, arguments=None):
	result = rpc("tools/call", {"name": name, "arguments": arguments or {}})["result"]
	return result.get("isError", False), json.loads(result["content"][0]["text"])


class TestDispatch(FrappeTestCase):
	def setUp(self):
		self.patches = [
			patch.dict(mcp.SCHEMAS, {"get_echo_test": schema("get_echo_test"), "get_boom_test": schema("get_boom_test")}),
			patch.dict(mcp.IMPLS, {}),
		]
		for p in self.patches:
			p.start()
		mcp.tool("get_echo_test")(lambda text: {"echo": text})

		def boom():
			frappe.get_doc({"doctype": "ToDo", "description": "mcp-rollback-probe"}).insert()
			raise frappe.ValidationError("boom")

		mcp.tool("get_boom_test")(boom)

	def tearDown(self):
		for p in reversed(self.patches):
			p.stop()

	def test_initialize_echoes_a_supported_version(self):
		r = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
			"clientInfo": {"name": "t", "version": "1"}})["result"]
		self.assertEqual(r["protocolVersion"], "2025-06-18")
		self.assertIn("tools", r["capabilities"])
		self.assertEqual(r["serverInfo"]["name"], "erpsmith")
		self.assertIn("RULE 10", r["instructions"])

	def test_initialize_unknown_version_gets_latest(self):
		r = rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]
		self.assertEqual(r["protocolVersion"], mcp.SUPPORTED_VERSIONS[0])

	def test_tools_list_gives_every_tool_a_title_and_a_hint(self):
		tools = rpc("tools/list")["result"]["tools"]
		self.assertIn("get_echo_test", [t["name"] for t in tools])
		for t in tools:
			self.assertTrue(t["title"], t["name"])
			self.assertTrue(t["annotations"]["title"], t["name"])
			self.assertIn("readOnlyHint", t["annotations"], t["name"])

	def test_tools_call_returns_json_text(self):
		self.assertEqual(call("get_echo_test", {"text": "hi"}), (False, {"echo": "hi"}))

	def test_unknown_tool_is_a_tool_error(self):
		err, body = call("nope_tool")
		self.assertTrue(err)
		self.assertIn("Unknown tool: nope_tool", body["message"])

	def test_wrong_arguments_are_a_tool_error_with_a_hint(self):
		err, body = call("get_echo_test", {"nope": 1})
		self.assertTrue(err)
		self.assertEqual(body["type"], "TypeError")
		self.assertIn("input schema", body["suggestion"])

	def test_a_database_error_returns_a_generic_message_and_an_error_log(self):
		def bad_sql():
			frappe.db.sql("select * from `tabNo Such Table`")

		self.register("get_bad_sql_test", bad_sql)
		before = set(frappe.get_all("Error Log", pluck="name"))
		err, body = call("get_bad_sql_test")
		created = set(frappe.get_all("Error Log", pluck="name")) - before
		for name in created:
			self.addCleanup(frappe.db.delete, "Error Log", {"name": name})
		self.assertTrue(err)
		self.assertNotIn("tabNo Such Table", json.dumps(body))
		self.assertEqual(body["type"], "ProgrammingError")
		match = re.fullmatch(re.escape(mcp.GENERIC_ERROR).replace(r"\{name\}", "(?P<name>.+)"), body["message"])
		self.assertTrue(match, body["message"])
		self.assertTrue(frappe.db.exists("Error Log", match["name"]))
		self.assertIn("get_error_logs", body["suggestion"])

	def test_failed_tool_rolls_back_its_writes(self):
		err, body = call("get_boom_test")
		self.assertTrue(err)
		self.assertEqual(body["message"], "boom")
		self.assertFalse(frappe.db.exists("ToDo", {"description": "mcp-rollback-probe"}))

	def register(self, name, fn):
		mcp.SCHEMAS[name] = schema(name)  # SCHEMAS and IMPLS are patched per test, so this is undone in tearDown
		mcp.tool(name)(fn)

	def test_failed_tool_discards_its_commit_callbacks(self):
		fired = []

		def queues_then_fails():
			frappe.db.before_commit.add(lambda: fired.append("before_commit"))
			frappe.db.after_commit.add(lambda: fired.append("after_commit"))
			raise frappe.ValidationError("boom")

		self.register("get_callbacks_test", queues_then_fails)
		self.assertTrue(call("get_callbacks_test")[0])
		# what the request's commit would run
		frappe.db.before_commit.run()
		frappe.db.after_commit.run()
		self.assertEqual(fired, [])

	def test_failed_tool_runs_its_rollback_callbacks_once(self):
		# a savepoint rollback never runs frappe.db.after_rollback by itself: File blobs and cache entries would be left behind
		self.addCleanup(frappe.db.after_rollback.reset)
		ran = []

		def queues_then_fails():
			frappe.db.after_rollback.add(lambda: ran.append("after_rollback"))
			raise frappe.ValidationError("boom")

		def queues_then_loses_its_savepoint():
			frappe.db.after_rollback.add(lambda: ran.append("after_rollback (full rollback)"))
			frappe.db.sql("release savepoint mcp_tool")
			raise frappe.ValidationError("boom")

		self.register("get_rollback_callbacks_test", queues_then_fails)
		self.register("get_rollback_callbacks_full_test", queues_then_loses_its_savepoint)
		self.assertTrue(call("get_rollback_callbacks_test")[0])
		self.assertEqual(ran, ["after_rollback"])
		self.assertTrue(call("get_rollback_callbacks_full_test")[0])
		self.assertEqual(ran, ["after_rollback", "after_rollback (full rollback)"])

	def test_failed_tool_leaves_no_file_blob_on_disk(self):
		self.addCleanup(frappe.db.after_rollback.reset)
		name = f"mcp-blob-probe-{secrets.token_hex(4)}.txt"
		path = frappe.get_site_path("private", "files", name)
		self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
		on_disk = []

		def saves_a_file_then_fails():
			frappe.get_doc({"doctype": "File", "file_name": name, "content": "probe", "is_private": 1}).insert()
			on_disk.append(os.path.exists(path))
			raise frappe.ValidationError("boom")

		self.register("get_blob_test", saves_a_file_then_fails)
		err, body = call("get_blob_test")
		self.assertTrue(err)
		self.assertEqual(body["message"], "boom")
		self.assertEqual(on_disk, [True])
		self.assertFalse(frappe.db.exists("File", {"file_name": name}))
		self.assertFalse(os.path.exists(path))

	def test_a_rollback_callback_that_raises_does_not_hide_the_tool_error_or_keep_commit_callbacks(self):
		self.addCleanup(frappe.db.after_rollback.reset)
		fired = []

		def fails_twice():
			def cleanup_breaks():
				raise OSError("disk gone")

			frappe.db.after_rollback.add(cleanup_breaks)
			frappe.db.after_commit.add(lambda: fired.append("after_commit"))
			raise frappe.ValidationError("boom")

		self.register("get_broken_cleanup_test", fails_twice)
		with patch("frappe.logger") as logger:
			err, body = call("get_broken_cleanup_test")
		self.assertTrue(err)
		self.assertEqual(body["message"], "boom")
		logger.return_value.warning.assert_called_once()
		frappe.db.after_commit.run()
		self.assertEqual(fired, [])

	def test_failed_tool_is_an_error_result_even_when_its_savepoint_is_gone(self):
		def loses_savepoint():
			frappe.get_doc({"doctype": "ToDo", "description": "mcp-lost-savepoint-probe"}).insert()
			frappe.db.sql("release savepoint mcp_tool")
			raise frappe.ValidationError("lost")

		self.register("get_lost_savepoint_test", loses_savepoint)
		err, body = call("get_lost_savepoint_test")
		self.assertTrue(err)
		self.assertEqual(body["message"], "lost")
		self.assertFalse(frappe.db.exists("ToDo", {"description": "mcp-lost-savepoint-probe"}))

	def test_an_error_after_a_lost_savepoint_says_some_changes_may_be_saved(self):
		def loses_savepoint():
			frappe.db.sql("release savepoint mcp_tool")  # what a commit or DDL statement inside the tool does
			raise frappe.ValidationError("half done")

		def keeps_savepoint():
			raise frappe.ValidationError("nothing done")

		self.register("get_partial_test", loses_savepoint)
		self.register("get_clean_failure_test", keeps_savepoint)
		err, body = call("get_partial_test")
		self.assertTrue(err)
		self.assertEqual(body["partial"], mcp.PARTIAL)
		err, body = call("get_clean_failure_test")
		self.assertTrue(err)
		self.assertNotIn("partial", body)

	def test_unserialisable_result_is_a_tool_error_and_rolls_back(self):
		def writes_then_returns_junk():
			frappe.get_doc({"doctype": "ToDo", "description": "mcp-unserialisable-probe"}).insert()
			return {(1, 2): "tuple keys are not JSON"}

		self.register("get_junk_result_test", writes_then_returns_junk)
		err, body = call("get_junk_result_test")
		self.assertTrue(err)
		self.assertEqual(body["type"], "TypeError")
		self.assertFalse(frappe.db.exists("ToDo", {"description": "mcp-unserialisable-probe"}))

	def test_registering_a_tool_twice_is_an_error(self):
		with self.assertRaises(ValueError):
			mcp.tool("get_echo_test")(lambda text: text)
		self.assertEqual(call("get_echo_test", {"text": "hi"}), (False, {"echo": "hi"}))

	def test_ping(self):
		self.assertEqual(rpc("ping")["result"], {})

	def test_unknown_method(self):
		self.assertEqual(rpc("nope/nope")["error"]["code"], -32601)

	def test_notification_gets_no_reply(self):
		self.assertIsNone(mcp.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}))

	def test_invalid_request_shapes(self):
		self.assertEqual(mcp.dispatch({"id": 1, "method": "ping"})["error"]["code"], -32600)
		self.assertEqual(mcp.dispatch({"jsonrpc": "2.0", "id": 1})["error"]["code"], -32600)
		self.assertEqual(mcp.dispatch(["not", "an", "object"])["error"]["code"], -32600)
		self.assertEqual(rpc("tools/call", params=["x"])["error"]["code"], -32602)

	def test_tools_call_name_must_be_a_string(self):
		for name in (None, ["x"], {"a": 1}, 7):
			self.assertEqual(rpc("tools/call", {"name": name})["error"]["code"], -32602, name)


class TestAnnotations(FrappeTestCase):
	def test_hints_follow_the_tool_name(self):
		ro = {"readOnlyHint": True}
		destructive = {"readOnlyHint": False, "destructiveHint": True}
		additive = {"readOnlyHint": False, "destructiveHint": False}
		cases = {
			"get_record": ro, "list_fields": ro, "query_records": ro, "search_memory": ro,
			"debug_workflow": ro, "full_health_check": ro, "dump_memory": ro, "get_current_plan": ro,
			"delete_record": destructive, "remove_field": destructive, "update_record": destructive,
			"set_permission": destructive, "fix_stuck_document": destructive, "apply_script_fix": destructive,
			"rename_doctype": destructive, "reorder_fields": destructive, "activate_workflow": destructive,
			"call_method": destructive, "call_method_read": ro,
			"create_record": additive, "add_field": additive, "submit_plan": additive, "approve_plan": additive,
		}
		with patch.dict(mcp.SCHEMAS, {n: schema(n) for n in cases}):
			for name, want in cases.items():
				got = mcp.annotations(name)
				self.assertEqual(got.pop("title"), mcp.SCHEMAS[name]["title"])
				self.assertEqual(got, want, name)


class TestHTTP(FrappeTestCase):
	def setUp(self):
		self.saved_request = getattr(frappe.local, "request", None)

	def tearDown(self):
		frappe.local.request = self.saved_request
		frappe.set_user("Administrator")

	def post(self, user, body, method="POST"):
		env = EnvironBuilder(method=method, path="/api/method/erpsmith.mcp.handle", data=body,
			content_type="application/json").get_environ()
		frappe.local.request = Request(env)
		frappe.set_user(user)
		return mcp.handle()

	def test_guest_gets_401_pointing_at_resource_metadata(self):
		r = self.post("Guest", "{}")
		self.assertEqual(r.status_code, 401)
		self.assertRegex(r.headers["WWW-Authenticate"],
			r'^Bearer resource_metadata="https?://[^"]+/\.well-known/oauth-protected-resource"$')

	def test_get_is_405(self):
		self.assertEqual(self.post("Administrator", None, method="GET").status_code, 405)

	def test_parse_error(self):
		r = self.post("Administrator", "{not json")
		self.assertEqual(json.loads(r.get_data())["error"]["code"], -32700)

	def test_notification_is_202(self):
		r = self.post("Administrator", json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
		self.assertEqual(r.status_code, 202)

	def test_ping_round_trip(self):
		r = self.post("Administrator", json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}))
		self.assertEqual(r.status_code, 200)
		self.assertEqual(r.mimetype, "application/json")
		self.assertEqual(json.loads(r.get_data()), {"jsonrpc": "2.0", "id": 7, "result": {}})

def request_at(path):
	frappe.local.request = Request(EnvironBuilder(method="POST", path=path).get_environ())


class TestChallengeHook(FrappeTestCase):
	"""after_request hook: Frappe v15 raises AuthenticationError for a bad bearer token before handle() runs."""

	def setUp(self):
		self.saved_request = getattr(frappe.local, "request", None)
		request_at(MCP_PATH)

	def tearDown(self):
		frappe.local.request = self.saved_request

	def test_it_is_registered_as_an_after_request_hook(self):
		self.assertIn("erpsmith.mcp.add_www_authenticate", frappe.get_hooks("after_request"))

	def test_a_401_on_the_mcp_path_gets_the_challenge(self):
		r = Response(status=401)
		mcp.add_www_authenticate(r)
		self.assertEqual(r.headers.getlist("WWW-Authenticate"), [mcp.www_authenticate()])
		self.assertRegex(r.headers["WWW-Authenticate"], CHALLENGE)

	def test_the_value_is_the_one_handle_gives_a_guest(self):
		frappe.local.request = Request(EnvironBuilder(method="POST", path=MCP_PATH, data="{}",
			content_type="application/json").get_environ())
		frappe.set_user("Guest")
		try:
			guest = mcp.handle()
		finally:
			frappe.set_user("Administrator")
		r = Response(status=401)
		mcp.add_www_authenticate(r)
		self.assertEqual(r.headers["WWW-Authenticate"], guest.headers["WWW-Authenticate"])

	def test_an_existing_challenge_is_left_alone(self):
		r = Response(status=401, headers={"WWW-Authenticate": 'Bearer resource_metadata="https://core.example/x"'})
		mcp.add_www_authenticate(r)
		self.assertEqual(r.headers.getlist("WWW-Authenticate"), ['Bearer resource_metadata="https://core.example/x"'])

	def test_other_statuses_and_other_paths_are_untouched(self):
		for status in (200, 202, 403, 405):
			r = Response(status=status)
			mcp.add_www_authenticate(r)
			self.assertNotIn("WWW-Authenticate", r.headers, status)
		for path in ("/api/method/ping", "/api/method/erpsmith.mcp.handle/x", "/"):
			request_at(path)
			r = Response(status=401)
			mcp.add_www_authenticate(r)
			self.assertNotIn("WWW-Authenticate", r.headers, path)

	def test_it_never_raises(self):
		mcp.add_www_authenticate(None)  # Frappe passes None when the request ended in an HTTPException
		frappe.local.request = None
		r = Response(status=401)
		mcp.add_www_authenticate(r)
		self.assertNotIn("WWW-Authenticate", r.headers)
		request_at(MCP_PATH)
		with patch.object(mcp, "www_authenticate", side_effect=RuntimeError("boom")), patch("frappe.logger") as logger:
			mcp.add_www_authenticate(Response(status=401))
		logger.return_value.warning.assert_called_once()

	def test_a_trailing_slash_on_the_site_url_does_not_double_the_slash(self):
		with patch("frappe.utils.get_url", return_value="https://erp.example.com/"):
			self.assertEqual(mcp.www_authenticate(),
				'Bearer resource_metadata="https://erp.example.com/.well-known/oauth-protected-resource"')
			frappe.set_user("Guest")
			try:
				guest = mcp.handle()
			finally:
				frappe.set_user("Administrator")
			self.assertEqual(guest.headers["WWW-Authenticate"], mcp.www_authenticate())


class TestChallengeOverWSGI(FrappeTestCase):
	"""Requests through the real WSGI app. It inits and destroys frappe.local per request, so each runs on a thread
	of its own; that thread has its own database connection and sees only committed rows."""

	def setUp(self):
		self.site = frappe.local.site
		self.client_id = None

	def tearDown(self):
		frappe.db.delete("OAuth Bearer Token", {"client": self.client_id or "-"})
		frappe.db.delete("OAuth Client", {"name": self.client_id or "-"})
		frappe.db.commit()

	def post(self, authorization=None, path=MCP_PATH, body='{"jsonrpc":"2.0","id":1,"method":"ping"}'):
		out = {}

		def run():
			try:
				headers = {"X-Frappe-Site-Name": self.site, "Content-Type": "application/json"}
				if authorization:
					headers["Authorization"] = authorization
				out["response"] = Client(frappe.app.application).post(path, data=body, headers=headers, buffered=True)
			except Exception as e:  # report it on the test thread
				out["error"] = e

		thread = threading.Thread(target=run)
		with patch("frappe.errprint"):  # developer mode prints the AuthenticationError traceback to stdout
			thread.start()
			thread.join()
		if "error" in out:
			raise out["error"]
		return out["response"]

	def token(self, status="Active", expires_in_minutes=60):
		if not self.client_id:
			self.client_id = oauth.register({"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]})["client_id"]
		value = secrets.token_hex(16)
		frappe.get_doc({
			"doctype": "OAuth Bearer Token", "client": self.client_id, "user": "Administrator", "scopes": "all",
			"access_token": value, "refresh_token": secrets.token_hex(16), "status": status,
			"expiration_time": add_to_date(now_datetime(), minutes=expires_in_minutes), "expires_in": 3600,
		}).insert(ignore_permissions=True)
		frappe.db.commit()
		return f"Bearer {value}"

	def assert_one_challenge(self, response):
		self.assertEqual(response.status_code, 401)
		challenges = response.headers.getlist("WWW-Authenticate")
		self.assertEqual(len(challenges), 1, challenges)
		self.assertRegex(challenges[0], CHALLENGE)

	def test_a_request_without_a_token_gets_one_challenge(self):
		self.assert_one_challenge(self.post())

	def test_an_unknown_bearer_token_gets_one_challenge(self):
		self.assert_one_challenge(self.post("Bearer not-a-token"))

	def test_an_expired_bearer_token_gets_one_challenge(self):
		self.assert_one_challenge(self.post(self.token(expires_in_minutes=-5)))

	def test_a_revoked_bearer_token_gets_one_challenge(self):
		self.assert_one_challenge(self.post(self.token(status="Revoked")))

	def test_a_valid_bearer_token_gets_a_reply_and_no_challenge(self):
		r = self.post(self.token())
		self.assertEqual(r.status_code, 200)
		self.assertEqual(json.loads(r.get_data()), {"jsonrpc": "2.0", "id": 1, "result": {}})
		self.assertNotIn("WWW-Authenticate", r.headers)
