from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from erpsmith import customization, knowledge, vectors
from erpsmith.tests import seed_rules
from erpsmith.tests.helpers import call, counted_sessions, drop_doctype, empty_knowledge_table, server_scripts

GADGET = "ERPSmith Test Gadget"
WORKFLOW = "ERPSmith Test Gadget Flow"
PREFIX = "ERPSmith Test"
TABLE = "MCP Knowledge Chunk"
SECRET = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5"


def keyed(chunks):
	return {c["chunk_key"]: c for c in chunks}


class TestCustomization(FrappeTestCase):
	"""One scratch DocType with a workflow for the whole class (they commit); each test runs inside a savepoint."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.get_doc({"doctype": "DocType", "name": GADGET, "module": "Custom", "custom": 1, "autoname": "hash",
			"fields": [{"fieldname": "title", "label": "Title", "fieldtype": "Data"},
				{"fieldname": "amount", "label": "Amount", "fieldtype": "Currency"}],
			"permissions": [{"role": "System Manager", "read": 1, "write": 1, "create": 1}]}).insert()
		cls.addClassCleanup(drop_doctype, GADGET)  # runs even when the Workflow insert below fails
		frappe.get_doc({"doctype": "Workflow", "workflow_name": WORKFLOW, "document_type": GADGET, "workflow_state_field": "workflow_state",
			"is_active": 1, "states": [{"state": "Pending", "doc_status": "0", "allow_edit": "System Manager"},
				{"state": "Approved", "doc_status": "0", "allow_edit": "System Manager"}],
			"transitions": [{"state": "Pending", "action": "Approve", "next_state": "Approved", "allowed": "System Manager"}]}).insert()
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		super().tearDownClass()
		if frappe.db.exists("Workflow", WORKFLOW):
			frappe.delete_doc("Workflow", WORKFLOW, force=True)

	def setUp(self):
		frappe.db.savepoint("customization_test")
		empty_knowledge_table()

	def tearDown(self):
		frappe.db.rollback(save_point="customization_test")
		frappe.clear_cache(doctype=GADGET)

	def server_script(self, name, code="doc.amount = 1", **values):
		values = {"script_type": "DocType Event", "reference_doctype": GADGET, "doctype_event": "Before Save", **values}
		with server_scripts():
			return frappe.get_doc({"doctype": "Server Script", "name": f"{PREFIX} {name}", "script": code, **values}).insert().name

	def client_script(self, name="Form", code="frappe.ui.form.on('x', { refresh(frm) {}, title(frm) {} });", **values):
		return frappe.get_doc({"doctype": "Client Script", "name": f"{PREFIX} {name}", "dt": GADGET, "view": "Form",
			"enabled": 1, "script": code, **values}).insert().name

	def notification(self, name="Note", **values):
		return frappe.get_doc({"doctype": "Notification", "name": f"{PREFIX} {name}", "subject": "Gadget saved", "document_type": GADGET,
			"event": "Save", "channel": "System Notification", "is_standard": 0,
			"recipients": [{"receiver_by_role": "System Manager"}, {"receiver_by_document_field": "owner"}, {"cc": "someone@example.com"}],
			"message": "Hello {{ doc.title }} BANK-ACC-123", **values}).insert().name

	def print_format(self, name="Format", **values):
		return frappe.get_doc({"doctype": "Print Format", "name": f"{PREFIX} {name}", "doc_type": GADGET, "standard": "No",
			"custom_format": 1, "print_format_type": "Jinja", "html": "<p>{{ doc.amount }}</p><p>IBAN XX99</p>", **values}).insert().name

	def test_each_source_becomes_a_chunk(self):
		event = self.server_script("Event")
		api = self.server_script("API", "frappe.response['message'] = 1", script_type="API", api_method="erpsmith_test_api",
			reference_doctype=None, doctype_event=None)
		scheduled = self.server_script("Daily", "pass", script_type="Scheduler Event", event_frequency="Daily",
			reference_doctype=None, doctype_event=None)
		with server_scripts():
			frappe.db.set_value("Server Script", event, "script", 'doc.amount = 1\nfrappe.get_doc("Note", "x")')
		client = self.client_script(enabled=0)
		notification, print_format = self.notification(), self.print_format()
		chunks = keyed(customization.customization_chunks())

		for doctype, name in (("Server Script", event), ("Server Script", api), ("Server Script", scheduled), ("Client Script", client),
				("Notification", notification), ("Print Format", print_format), ("Workflow", WORKFLOW)):
			chunk = chunks[f"customization::{doctype}::{name}"]
			self.assertEqual((chunk["kind"], chunk["title"]), ("customization", name))
		script = chunks[f"customization::Server Script::{event}"]
		self.assertEqual(script["source"], f"/app/server-script/{event.replace(' ', '%20')}")
		for word in ("Before Save", "Note", "amount"):
			self.assertIn(word, script["summary"])
		self.assertIn("erpsmith_test_api", chunks[f"customization::Server Script::{api}"]["content"])
		self.assertIn("Daily", chunks[f"customization::Server Script::{scheduled}"]["content"])

		client = chunks[f"customization::Client Script::{client}"]
		self.assertIn("[disabled]", client["content"])
		self.assertIn("refresh", client["summary"])
		self.assertIn("title", client["summary"])

		text = chunks[f"customization::Notification::{notification}"]["content"]
		for word in ("System Manager", "owner", "title"):
			self.assertIn(word, text)
		for word in ("someone@example.com", "BANK-ACC-123"):
			self.assertNotIn(word, text)

		text = chunks[f"customization::Print Format::{print_format}"]["content"]
		self.assertIn("amount", text)
		self.assertNotIn("IBAN", text)

		flow = chunks[f"customization::Workflow::{WORKFLOW}"]["content"]
		self.assertIn("Pending --Approve (System Manager)--> Approved", flow)
		self.assertIn("Approved", flow.replace("Pending --Approve (System Manager)--> Approved", ""))

	def test_standard_notifications_and_print_formats_are_skipped(self):
		notification, print_format = self.notification(), self.print_format()
		# inserting them as standard on a developer-mode site would export files into an app folder
		frappe.db.set_value("Notification", notification, "is_standard", 1)
		frappe.db.set_value("Print Format", print_format, "standard", "Yes")
		keys = set(keyed(customization.customization_chunks()))
		self.assertNotIn(f"customization::Notification::{notification}", keys)
		self.assertNotIn(f"customization::Print Format::{print_format}", keys)

	def test_a_planted_secret_never_reaches_a_chunk_or_a_search_result(self):
		self.server_script("Secret", f'api_key = "{SECRET}"\ndoc.amount = 2')
		knowledge.sync(customization.customization_chunks(), ("customization",))
		for row in frappe.get_all(TABLE, fields=["content", "summary"]):
			self.assertNotIn(SECRET, row.content)
			self.assertNotIn(SECRET, row.summary)
		self.assertTrue(frappe.db.exists(TABLE, f"customization::Server Script::{PREFIX} Secret"))
		err, out = call("search_erpnext_knowledge", {"query": "api key in a server script", "kind": "customization"})
		self.assertFalse(err, out)
		self.assertTrue(out["results"])
		self.assertNotIn(SECRET, " ".join(r["content"] for r in out["results"]))

	def test_the_doctype_chunk_lists_what_is_attached(self):
		event = self.server_script("Event")
		client = self.client_script()
		line = customization.attachments()[GADGET]
		self.assertTrue(line.startswith("Customizations: "), line)
		self.assertIn(f'Workflow "{WORKFLOW}" (active)', line)
		self.assertIn(f"{event} (Before Save)", line)
		self.assertIn(f"{client} (Form)", line)
		chunk = knowledge.doctype_chunks(only=[GADGET])[0]
		self.assertIn(line, chunk["content"])
		self.assertNotIn("Customizations:", chunk["summary"])

	def test_hash_skip_and_deletion(self):
		name = self.server_script("Event")
		with counted_sessions() as built:
			knowledge.build(include_manual=False, commit=False)
			self.assertEqual(len(built), 1)
		with patch.object(vectors, "embed", wraps=vectors.embed) as embed:
			knowledge.build(include_manual=False, commit=False)
		self.assertEqual(embed.call_count, 0)

		frappe.db.set_value("Server Script", name, "doctype_event", "After Save")
		with patch.object(vectors, "embed", wraps=vectors.embed) as embed:
			knowledge.build(include_manual=False, commit=False)
		self.assertEqual(embed.call_count, 2)  # its chunk, and its DocType's chunk whose Customizations line changed

		frappe.db.set_value("Server Script", name, "script", "doc.amount = 5")
		with patch.object(vectors, "embed", wraps=vectors.embed) as embed:
			knowledge.build(include_manual=False, commit=False)
		self.assertEqual(embed.call_count, 1)

		key = f"customization::Server Script::{name}"
		self.assertTrue(frappe.db.exists(TABLE, key))
		frappe.db.delete("Server Script", {"name": name})
		out = knowledge.build(include_manual=False, commit=False)
		self.assertEqual(out["deleted"], 1)
		self.assertFalse(frappe.db.exists(TABLE, key))

	def test_search_takes_kind_customization(self):
		self.server_script("Event")
		knowledge.sync(customization.customization_chunks() + knowledge.doctype_chunks(only=[GADGET]), ("customization", "doctype"))
		err, out = call("search_erpnext_knowledge", {"query": "script that sets the amount", "kind": "customization", "topK": 10})
		self.assertFalse(err, out)
		self.assertTrue(out["results"])
		self.assertEqual({r["kind"] for r in out["results"]}, {"customization"})
		err, out = call("search_erpnext_knowledge", {"query": "x", "kind": "nope"})
		self.assertEqual(out["type"], "InvalidArguments")
		self.assertIn("customization", out["message"])

	def test_generated_text_is_descriptive(self):
		self.server_script("Event")
		self.client_script()
		self.notification()
		self.print_format()
		chunks = [c for c in customization.customization_chunks() if c["title"].startswith(PREFIX)]  # not the bench's own records
		self.assertTrue(len(chunks) >= 5)
		for chunk in chunks:
			self.assertNotIn("directive", seed_rules.hard_violations(chunk["summary"]), chunk["chunk_key"])

	def test_one_failing_source_does_not_stop_the_others(self):
		self.server_script("Event")
		self.client_script()
		with patch.object(customization, "_client_script_chunks", side_effect=RuntimeError("bad row")), \
				patch.object(customization, "_attach_notifications", side_effect=RuntimeError("bad row")), \
				patch("erpsmith.customization.frappe.log_error") as log:
			keys = set(keyed(customization.customization_chunks()))
			line = customization.attachments()[GADGET]
		self.assertIn(f"customization::Server Script::{PREFIX} Event", keys)
		self.assertIn(f"customization::Workflow::{WORKFLOW}", keys)
		self.assertFalse(any(k.startswith("customization::Client Script::") for k in keys))
		self.assertIn("Server Scripts:", line)
		self.assertEqual(sorted(c.kwargs["title"] for c in log.call_args_list), [
			"ERPSmith: customization chunks for Client Script failed", "ERPSmith: customization chunks for Notification failed"])

	def test_a_workflow_state_update_value_is_scrubbed(self):
		frappe.db.set_value("Workflow Document State", {"parent": WORKFLOW, "state": "Pending"},
			{"update_field": "title", "update_value": 'api_key = "' + SECRET + '"'})
		content = keyed(customization.customization_chunks())[f"customization::Workflow::{WORKFLOW}"]["content"]
		self.assertIn("sets title", content)
		self.assertNotIn(SECRET, content)
