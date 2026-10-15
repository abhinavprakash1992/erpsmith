import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from erpsmith import mcp


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
			patch.dict(mcp.SCHEMAS, {"echo_test": schema("echo_test"), "boom_test": schema("boom_test")}),
			patch.dict(mcp.IMPLS, {}),
		]
		for p in self.patches:
			p.start()
		mcp.tool("echo_test")(lambda text: {"echo": text})

		def boom():
			frappe.get_doc({"doctype": "ToDo", "description": "mcp-rollback-probe"}).insert()
			raise frappe.ValidationError("boom")

		mcp.tool("boom_test")(boom)

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
		self.assertIn("echo_test", [t["name"] for t in tools])
		for t in tools:
			self.assertTrue(t["title"], t["name"])
			self.assertTrue(t["annotations"]["title"], t["name"])
			self.assertIn("readOnlyHint", t["annotations"], t["name"])

	def test_tools_call_returns_json_text(self):
		self.assertEqual(call("echo_test", {"text": "hi"}), (False, {"echo": "hi"}))

	def test_unknown_tool_is_a_tool_error(self):
		err, body = call("nope_tool")
		self.assertTrue(err)
		self.assertIn("Unknown tool: nope_tool", body["message"])

	def test_wrong_arguments_are_a_tool_error_with_a_hint(self):
		err, body = call("echo_test", {"nope": 1})
		self.assertTrue(err)
		self.assertEqual(body["type"], "TypeError")
		self.assertIn("input schema", body["suggestion"])

	def test_failed_tool_rolls_back_its_writes(self):
		err, body = call("boom_test")
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

		self.register("callbacks_test", queues_then_fails)
		self.assertTrue(call("callbacks_test")[0])
		# what the request's commit would run
		frappe.db.before_commit.run()
		frappe.db.after_commit.run()
		self.assertEqual(fired, [])

	def test_failed_tool_is_an_error_result_even_when_its_savepoint_is_gone(self):
		def loses_savepoint():
			frappe.get_doc({"doctype": "ToDo", "description": "mcp-lost-savepoint-probe"}).insert()
			frappe.db.sql("release savepoint mcp_tool")
			raise frappe.ValidationError("lost")

		self.register("lost_savepoint_test", loses_savepoint)
		err, body = call("lost_savepoint_test")
		self.assertTrue(err)
		self.assertEqual(body["message"], "lost")
		self.assertFalse(frappe.db.exists("ToDo", {"description": "mcp-lost-savepoint-probe"}))

	def test_unserialisable_result_is_a_tool_error_and_rolls_back(self):
		def writes_then_returns_junk():
			frappe.get_doc({"doctype": "ToDo", "description": "mcp-unserialisable-probe"}).insert()
			return {(1, 2): "tuple keys are not JSON"}

		self.register("junk_result_test", writes_then_returns_junk)
		err, body = call("junk_result_test")
		self.assertTrue(err)
		self.assertEqual(body["type"], "TypeError")
		self.assertFalse(frappe.db.exists("ToDo", {"description": "mcp-unserialisable-probe"}))

	def test_registering_a_tool_twice_is_an_error(self):
		with self.assertRaises(ValueError):
			mcp.tool("echo_test")(lambda text: text)
		self.assertEqual(call("echo_test", {"text": "hi"}), (False, {"echo": "hi"}))

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
