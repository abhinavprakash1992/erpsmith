import json
from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, get_datetime, now_datetime

from erpsmith import guards, mcp
from erpsmith.guards import DangerousOp, GateBlocked

OTHER = "mcp-other@example.com"


def upd(name, value):
	return {"doctype": "Delivery Note", "name": name, "data": {"remarks": value}}


def plan(*changes):
	return guards.submit_plan("test", [{"tool": t, "args": a, "reason": "r"} for t, a in changes])["planId"]


class TestPlanGate(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("User", OTHER):
			frappe.get_doc({"doctype": "User", "email": OTHER, "first_name": "MCP Other",
				"send_welcome_email": 0}).insert(ignore_permissions=True)

	def setUp(self):
		frappe.set_user("Administrator")
		for name in frappe.get_all("MCP Plan", filters={"owner": ("in", ["Administrator", OTHER])}, pluck="name"):
			frappe.delete_doc("MCP Plan", name, ignore_permissions=True, force=True)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_no_plan_blocks_writes_but_not_reads(self):
		with self.assertRaisesRegex(GateBlocked, "No plan has been submitted"):
			guards.check_gate("update_record", upd("DN-0", "x"))
		guards.check_gate("get_record", {"doctype": "Delivery Note", "name": "DN-0"})
		guards.check_gate("call_method_read", {"method": "frappe.ping"})
		guards.check_gate("add_memory", {"topic": "pattern", "title": "t", "content": "c"})

	def test_unapproved_plan_blocks_its_own_write(self):
		plan_id = plan(("update_record", upd("DN-P", "p")))
		with self.assertRaisesRegex(GateBlocked, f"plan {plan_id} is 'pending_approval'"):
			guards.check_gate("update_record", upd("DN-P", "p"))

	def test_two_plans_do_not_overwrite_each_other(self):
		a = plan(("update_record", upd("DN-A", "a")))
		b = plan(("update_record", upd("DN-B", "b")))
		self.assertNotEqual(a, b)
		guards.approve_plan(a)
		guards.check_gate("update_record", upd("DN-A", "a"))
		with self.assertRaisesRegex(GateBlocked, "does not match any remaining approved change"):
			guards.check_gate("update_record", upd("DN-B", "b"))
		guards.approve_plan(b)
		guards.check_gate("update_record", upd("DN-B", "b"))
		self.assertEqual(guards.get_current_plan()["plan"]["planId"], b)

	def test_args_must_match_exactly_and_run_once(self):
		guards.approve_plan(plan(("update_record", upd("DN-C", "c"))))
		with self.assertRaisesRegex(GateBlocked, "does not match"):
			guards.check_gate("update_record", upd("DN-C", "different"))
		guards.check_gate("update_record", {"data": {"remarks": "c"}, "name": "DN-C", "doctype": "Delivery Note"})
		with self.assertRaisesRegex(GateBlocked, "does not match"):
			guards.check_gate("update_record", upd("DN-C", "c"))

	def test_unknown_plan_and_expired_approval(self):
		with self.assertRaisesRegex(frappe.DoesNotExistError, "Unknown planId plan_nope"):
			guards.approve_plan("plan_nope")
		expiring = plan(("create_record", {"doctype": "Note", "data": {"title": "d"}}))
		guards.approve_plan(expiring)
		frappe.db.set_value("MCP Plan", expiring, "expires_at", add_to_date(now_datetime(), minutes=-1))
		with self.assertRaisesRegex(GateBlocked, "expired"):
			guards.check_gate("create_record", {"doctype": "Note", "data": {"title": "d"}})

	def test_plans_are_per_user(self):
		admin_plan = plan(("update_record", upd("DN-U", "u")))
		guards.approve_plan(admin_plan)
		frappe.set_user(OTHER)
		with self.assertRaisesRegex(GateBlocked, "No plan has been submitted"):
			guards.check_gate("update_record", upd("DN-U", "u"))
		with self.assertRaisesRegex(frappe.DoesNotExistError, f"Unknown planId {admin_plan}"):
			guards.approve_plan(admin_plan)
		self.assertEqual(guards.get_current_plan(), {"plan": None})

	def test_reapproving_cannot_unconsume_a_step_another_worker_took(self):
		# approve_plan used plan.save(), which rewrites every child row from the document it loaded: a re-approval
		# that read the plan before a gated call consumed its step wrote consumed=0 back and the call could be replayed
		args = upd("DN-S", "s")
		plan_id = plan(("update_record", args))
		guards.approve_plan(plan_id)
		stale = frappe.get_doc("MCP Plan", plan_id)  # the re-approving worker's read: the step is still unused
		self.assertFalse(stale.changes[0].consumed)
		guards.check_gate("update_record", args)  # a gated call, in another worker, uses the step
		real_get_doc, served = frappe.get_doc, []

		def get_doc(*a, **kw):
			if a == ("MCP Plan", plan_id) and not kw and not served:
				served.append(1)
				return stale
			return real_get_doc(*a, **kw)

		with patch("frappe.get_doc", get_doc):
			result = guards.approve_plan(plan_id)
		self.assertTrue(guards.get_current_plan()["plan"]["changes"][0]["consumed"])
		self.assertEqual(result["changesRemaining"], 0)
		with self.assertRaisesRegex(GateBlocked, "does not match"):
			guards.check_gate("update_record", args)

	def test_approve_plan_returns_the_same_shape_and_marks_the_plan_approved(self):
		plan_id = plan(("update_record", upd("DN-V", "v")), ("update_record", upd("DN-W", "w")))
		before = frappe.db.get_value("MCP Plan", plan_id, "modified")
		r = guards.approve_plan(plan_id)
		self.assertEqual(sorted(r), ["approved", "approvedAt", "changesRemaining", "expiresAt", "planId"])
		self.assertEqual((r["approved"], r["planId"], r["changesRemaining"]), (True, plan_id, 2))
		self.assertEqual(get_datetime(r["expiresAt"]) - get_datetime(r["approvedAt"]), timedelta(minutes=15))
		row = frappe.db.get_value("MCP Plan", plan_id, ["status", "approved_at", "expires_at", "modified"], as_dict=True)
		self.assertEqual(row.status, "Approved")
		self.assertEqual((str(row.approved_at), str(row.expires_at)), (r["approvedAt"], r["expiresAt"]))
		self.assertGreaterEqual(row.modified, before)

	def test_plan_id_must_be_a_string(self):
		# a list or dict used to reach the ownership filter and an unfiltered get_doc: ["in", [...]] raised a raw SQL error
		mine = plan(("update_record", upd("DN-I", "i")))
		for bad in (["like", "%"], ["in", [mine]], {"name": mine}, 7, 1.5, True, None):
			with self.subTest(planId=bad):
				r = mcp.call_tool("approve_plan", {"planId": bad})
				self.assertTrue(r["isError"])
				body = json.loads(r["content"][0]["text"])
				self.assertEqual((body["type"], body["message"]), ("ValueError", "planId must be a string"))
		for unknown in ("", "%", "plan_nope"):
			with self.assertRaises(frappe.DoesNotExistError, msg=unknown):
				guards.approve_plan(unknown)
		self.assertEqual(frappe.db.get_value("MCP Plan", mine, "status"), "Pending Approval")
		self.assertTrue(guards.approve_plan(mine)["approved"])

	def test_ttl_minutes_must_be_a_number_from_1_to_1440(self):
		for ttl in (1, 1.5, 15, 1440, 1440.0):
			with self.subTest(accepted=ttl):
				r = guards.approve_plan(plan(("update_record", upd("DN-L", "l"))), ttlMinutes=ttl)
				self.assertEqual(get_datetime(r["expiresAt"]) - get_datetime(r["approvedAt"]), timedelta(minutes=ttl))
		for omitted in ({}, {"ttlMinutes": None}):
			with self.subTest(omitted=omitted):
				r = guards.approve_plan(plan(("update_record", upd("DN-D", "d"))), **omitted)
				self.assertEqual(get_datetime(r["expiresAt"]) - get_datetime(r["approvedAt"]), timedelta(minutes=15))
		for ttl in (0, 0.5, 1441, -5, 1e9, "15", "", True, False, float("nan"), float("inf"), float("-inf"), [15], {"m": 15}):
			with self.subTest(rejected=ttl):
				plan_id = plan(("update_record", upd("DN-X", "x")))
				with self.assertRaisesRegex(ValueError, "^ttlMinutes must be a number between 1 and 1440$"):
					guards.approve_plan(plan_id, ttlMinutes=ttl)
				self.assertEqual(frappe.db.get_value("MCP Plan", plan_id, "status"), "Pending Approval")
		r = mcp.call_tool("approve_plan", {"planId": plan(("update_record", upd("DN-Y", "y"))), "ttlMinutes": "15"})
		self.assertTrue(r["isError"])
		self.assertIn("ttlMinutes must be a number", json.loads(r["content"][0]["text"])["message"])

	def test_get_current_plan_shows_consumption(self):
		plan_id = plan(("update_record", upd("DN-G", "g")))
		guards.approve_plan(plan_id)
		guards.check_gate("update_record", upd("DN-G", "g"))
		current = guards.get_current_plan()["plan"]
		self.assertEqual(current["status"], "approved")
		self.assertEqual(current["changes"][0]["args"], upd("DN-G", "g"))
		self.assertTrue(current["changes"][0]["consumed"])

	def test_failed_tool_does_not_consume_plan_step(self):
		fail = {"name": "update_probe", "title": "Update Probe", "description": "t", "inputSchema": {"type": "object"}}
		with patch.dict(mcp.SCHEMAS, {"update_probe": fail}), patch.dict(mcp.IMPLS, {}):
			def probe(x):
				raise frappe.ValidationError("probe failed")

			mcp.tool("update_probe")(probe)
			guards.approve_plan(plan(("update_probe", {"x": 1})))
			r = mcp.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
				"params": {"name": "update_probe", "arguments": {"x": 1}}})["result"]
			self.assertTrue(r["isError"])
			self.assertFalse(guards.get_current_plan()["plan"]["changes"][0]["consumed"])

	def test_approved_write_runs_once_through_call_tool(self):
		args = {"x": 1}
		with patch.dict(mcp.IMPLS, {"update_probe": lambda x: {"ok": x}}):
			guards.approve_plan(plan(("update_probe", args)))
			self.assertNotIn("isError", mcp.call_tool("update_probe", args))
			self.assertTrue(mcp.call_tool("update_probe", args)["isError"])

	def test_dangerous_op_is_blocked_even_when_approved_and_keeps_the_step(self):
		args = {"doctype": "Company", "name": "X"}
		ran = []
		with patch.dict(mcp.IMPLS, {"delete_record": lambda **kw: ran.append(kw)}):
			guards.approve_plan(plan(("delete_record", args)))
			r = mcp.call_tool("delete_record", args)
			self.assertTrue(r["isError"])
			self.assertIn("DANGEROUS OP BLOCKED", json.loads(r["content"][0]["text"])["message"])
			self.assertEqual(ran, [])
			self.assertFalse(guards.get_current_plan()["plan"]["changes"][0]["consumed"])

	def _read_then_lose_row(self, only_first=False):
		"""Stand in for another worker consuming a change between our read and our UPDATE:
		our snapshot still says consumed=0, the database already says 1."""
		real = guards._unused_changes

		def stale(plan_names):
			rows = real(plan_names)
			for row in rows[:1] if only_first else rows:
				frappe.db.set_value("MCP Plan Change", row.name, "consumed", 1)
			return rows

		return patch.object(guards, "_unused_changes", stale)

	def test_change_consumed_by_another_request_is_not_consumed_twice(self):
		args = upd("DN-R", "r")
		guards.approve_plan(plan(("update_record", args)))
		with self._read_then_lose_row(), self.assertRaisesRegex(GateBlocked, "does not match"):
			guards.check_gate("update_record", args)

	def test_lost_race_falls_through_to_the_next_identical_change(self):
		args = upd("DN-F", "f")
		a = plan(("update_record", args))
		b = plan(("update_record", args))
		guards.approve_plan(a)
		guards.approve_plan(b)
		with self._read_then_lose_row(only_first=True):
			guards.check_gate("update_record", args)
		self.assertFalse(frappe.get_all("MCP Plan Change", filters={"parent": ("in", [a, b]), "consumed": 0}))
		with self.assertRaisesRegex(GateBlocked, "does not match"):
			guards.check_gate("update_record", args)

	def test_identical_changes_in_two_plans_allow_exactly_two_calls(self):
		args = upd("DN-T", "t")
		guards.approve_plan(plan(("update_record", args)))
		guards.approve_plan(plan(("update_record", args)))
		guards.check_gate("update_record", args)
		guards.check_gate("update_record", args)
		with self.assertRaisesRegex(GateBlocked, "does not match"):
			guards.check_gate("update_record", args)


class TestDangerousOps(FrappeTestCase):
	def test_user_security_fields_are_blocked(self):
		for tool in ("create_record", "update_record"):
			with self.assertRaisesRegex(DangerousOp, "DANGEROUS OP BLOCKED.*User.roles"):
				guards.check_dangerous_op(tool, {"doctype": "User", "name": "x", "data": {"roles": []}})
		guards.check_dangerous_op("update_record", {"doctype": "User", "name": "x", "data": {"first_name": "A"}})

	def test_core_doctypes_cannot_be_deleted(self):
		with self.assertRaisesRegex(DangerousOp, "Company"):
			guards.check_dangerous_op("delete_record", {"doctype": "Company", "name": "X"})
		guards.check_dangerous_op("delete_record", {"doctype": "ToDo", "name": "X"})

	def test_bulk_delete_over_10_is_blocked(self):
		bulk = lambda n: {"method": "frappe.client.delete", "args": {"names": [str(i) for i in range(n)]}}
		with self.assertRaisesRegex(DangerousOp, "11 records"):
			guards.check_dangerous_op("call_method", bulk(11))
		guards.check_dangerous_op("call_method", bulk(10))

	def test_set_value_on_child_tables_is_blocked(self):
		sv = lambda f, **extra: {"method": "frappe.client.set_value", "args": {"doctype": "User", "name": "x", "fieldname": f, **extra}}
		for fieldname in ("roles", "items", {"a": 1}, ["roles"], '{"roles": []}', '{"first_name": "A"}'):
			with self.assertRaises(DangerousOp, msg=fieldname):
				guards.check_dangerous_op("call_method", sv(fieldname))
		guards.check_dangerous_op("call_method", sv("first_name"))
		guards.check_dangerous_op("call_method", sv("first_name", value="A"))
		guards.check_dangerous_op("call_method", sv('{"x"', value="A"))  # not JSON, and a value is given: one plain field

	def test_list_view_bulk_delete_over_10_is_blocked(self):
		items = lambda n: {"method": "frappe.desk.reportview.delete_items", "args": {"doctype": "ToDo",
			"items": json.dumps([str(i) for i in range(n)])}}
		with self.assertRaisesRegex(DangerousOp, "11 records"):
			guards.check_dangerous_op("call_method", items(11))
		guards.check_dangerous_op("call_method", items(10))
		guards.check_dangerous_op("call_method_read", items(11))  # the guard checks call_method only; call_method_read refuses background jobs

	def test_unusual_argument_shapes_do_not_crash_the_guard(self):
		for args in ({"method": "frappe.client.set_value", "args": "x"}, {"method": "frappe.client.set_value", "args": None},
				{"doctype": "User", "data": "password"}, {"method": "frappe.desk.reportview.delete_items", "args": {"items": "not json"}}):
			guards.check_dangerous_op("call_method", args)
			guards.check_dangerous_op("update_record", args)
		for doctype in (["User"], {"a": 1}, None):  # delete_record with a doctype that is not a string
			guards.check_dangerous_op("delete_record", {"doctype": doctype, "name": "x"})


class TestPlanToolsOverMCP(FrappeTestCase):
	def test_plan_tools_are_registered(self):
		names = {t["name"] for t in mcp.list_tools()}
		self.assertTrue({"submit_plan", "approve_plan", "get_current_plan"} <= names)

	def test_submit_plan_rejects_empty_changes(self):
		r = mcp.call_tool("submit_plan", {"summary": "s", "changes": []})
		self.assertTrue(r["isError"])
		self.assertIn("submit_plan requires", json.loads(r["content"][0]["text"])["message"])
