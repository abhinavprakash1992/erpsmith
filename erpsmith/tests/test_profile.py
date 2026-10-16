from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from erpsmith import knowledge, memory, profile, vectors
from erpsmith.tests import seed_rules
from erpsmith.tests.helpers import call, drop_doctype, empty_memory_table

GADGET = "ERPSmith Test Profile Gadget"
FLOW = "ERPSmith Test Flow"
FRAPPE_ONLY = ["currencies", "naming-series", "apps", "workflows", "footprint"]


def stored(key):
	return frappe.db.get_value("MCP Memory", f"profile__{key}", ["title", "content", "topic", "seed_key"], as_dict=True)


class TestFormatters(FrappeTestCase):
	def test_formatters_on_synthetic_erpnext_rows(self):
		co = {"name": "ERPSmith Test Co", "abbr": "ETC", "country": "India", "default_currency": "INR", "parent_company": None}
		text = profile.format_companies([co], "2026-2027")
		self.assertTrue(text.startswith(profile.HEADER))
		for part in ("ERPSmith Test Co (ETC)", "India", "INR", "2026-2027"):
			self.assertIn(part, text)
		many = profile.format_companies([{**co, "name": f"ERPSmith Test Co {i}"} for i in range(25)], None)
		self.assertEqual(many.count("- ERPSmith Test Co "), 20)
		self.assertIn("and 5 more", many)
		wh = profile.format_warehouses([{"name": "ERPSmith Test Stores", "children": 3}, {"name": "ERPSmith Test Depot", "children": 1}],
			"ERPSmith Test Stores - ETC", "FIFO")
		for part in ("ERPSmith Test Stores: 3 child", "ERPSmith Test Depot: 1 child", "Default warehouse: ERPSmith Test Stores - ETC", "FIFO"):
			self.assertIn(part, wh)
		cur = profile.format_currencies("INR", ["INR", "USD"], ["USD"])
		self.assertTrue(cur.startswith(profile.HEADER))
		for part in ("Default currency (Global Defaults): INR", "INR, USD", "used by Price Lists: USD"):
			self.assertIn(part, cur)

	def test_naming_series_keep_the_given_order_and_cap_the_list(self):
		series = {f"ERPSmith Test Doc {i:02d}": [f"ETD{i}-.YYYY.-"] for i in range(45, 0, -1)}
		text = profile.format_naming_series(series)
		self.assertEqual(text.count("\n- "), 40)
		self.assertIn("and 5 more", text)
		self.assertLess(text.index("Doc 45"), text.index("Doc 06"))  # most rows first: the caller's order stands
		self.assertNotIn("Doc 05", text)

	def test_other_formatters_open_with_the_header(self):
		for text in (profile.format_naming_series({"ERPSmith Test Doc": ["ETD-.YYYY.-"]}), profile.format_naming_series({}),
				profile.format_apps([{"name": "frappe", "version": "15.0.0"}], {"frappe": ["Core"]}), profile.format_workflows([]),
				profile.format_workflows([{"name": "F", "document_type": "D", "is_active": 1, "states": ["A", "B"]}]),
				profile.format_footprint(["acme"], {"custom fields": 2}, [("ToDo", 2)])):
			self.assertTrue(text.startswith(profile.HEADER))
			self.assertEqual(seed_rules.hard_violations(text), [])


class TestProfile(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.get_doc({"doctype": "DocType", "name": GADGET, "module": "Custom", "custom": 1, "autoname": "hash",
			"fields": [{"fieldname": "workflow_state", "label": "Status", "fieldtype": "Link", "options": "Workflow State"}],
			"permissions": [{"role": "System Manager", "read": 1}]}).insert()
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		super().tearDownClass()
		if frappe.db.exists("Workflow", FLOW):
			frappe.delete_doc("Workflow", FLOW, force=True)
			frappe.db.commit()
		drop_doctype(GADGET)

	def setUp(self):
		frappe.db.savepoint("profile_test")
		empty_memory_table()

	def tearDown(self):
		frappe.db.rollback(save_point="profile_test")
		frappe.clear_cache(doctype=GADGET)

	def test_existence_is_by_doctype_record_so_single_doctypes_count(self):
		self.assertTrue(profile._have("Currency"))
		self.assertTrue(profile._have("System Settings"))  # a Single DocType has no table
		self.assertFalse(profile._have("Company"))

	def test_a_failing_note_is_logged_and_skipped(self):
		with patch.object(profile, "_apps", side_effect=RuntimeError("boom")), patch("frappe.log_error") as log:
			keys = [n["key"] for n in profile.profile_notes()]
			counts = knowledge.build(include_manual=False, commit=False)
		self.assertEqual(keys, [k for k in FRAPPE_ONLY if k != "apps"])
		self.assertEqual(log.call_args.kwargs["title"], "ERPSmith: site profile note apps failed")
		self.assertEqual(sum(counts["profile"].values()), len(FRAPPE_ONLY) - 1)

	def test_a_site_without_erpnext_gets_only_the_frappe_notes(self):
		self.assertEqual([n["key"] for n in profile.profile_notes()], FRAPPE_ONLY)
		counts = knowledge.build(include_manual=False, commit=False)
		self.assertEqual(sum(counts["profile"].values()), len(FRAPPE_ONLY))

	def test_notes_are_stored_with_profile_keys(self):
		profile.sync_profile(profile.profile_notes())
		for key in FRAPPE_ONLY:
			row = stored(key)
			self.assertEqual((row.title, row.topic, row.seed_key), (profile.TITLES[key], "context", f"profile-{key}"))
			self.assertTrue(row.content.startswith(profile.HEADER))

	def test_a_rerun_embeds_nothing_and_a_deleted_note_returns(self):
		profile.sync_profile(profile.profile_notes())
		real = vectors.embed
		with patch("erpsmith.vectors.embed", side_effect=real) as embed:
			again = profile.sync_profile(profile.profile_notes())
		self.assertEqual((again, embed.call_count), ({"added": 0, "updated": 0, "unchanged": 5}, 0))
		frappe.delete_doc("MCP Memory", "profile__apps")
		self.assertEqual(profile.sync_profile(profile.profile_notes())["added"], 1)
		self.assertTrue(stored("apps"))
		frappe.db.set_value("MCP Memory", "profile__apps", "content", "edited by hand")
		self.assertEqual(profile.sync_profile(profile.profile_notes())["updated"], 1)
		self.assertTrue(stored("apps").content.startswith(profile.HEADER))

	def test_a_new_workflow_changes_only_the_workflows_note(self):
		profile.sync_profile(profile.profile_notes())
		frappe.get_doc({"doctype": "Workflow", "workflow_name": FLOW, "document_type": GADGET, "workflow_state_field": "workflow_state",
			"is_active": 0, "states": [{"state": "Pending", "doc_status": "0", "allow_edit": "System Manager"}], "transitions": []}).insert()
		self.assertEqual(profile.sync_profile(profile.profile_notes()), {"added": 0, "updated": 1, "unchanged": 4})
		self.assertIn(FLOW, stored("workflows").content)

	def test_notes_are_descriptive_and_findable(self):
		notes = profile.profile_notes()
		for note in notes:
			self.assertEqual(seed_rules.hard_violations(note["content"]), [], note["key"])
		profile.sync_profile(notes)
		frappe.get_doc({"doctype": "MCP Memory", "topic": "context", "title": "Unrelated", "content": "Something else entirely."}).insert()
		is_error, out = call("search_memory", {"query": "which apps are installed on this site", "topic": "context"})
		self.assertFalse(is_error)
		self.assertIn("profile__apps", [r["id"] for r in out["results"][:3]])

	def test_seed_files_cannot_use_profile_keys(self):
		with self.assertRaisesRegex(ValueError, "profile-"):
			memory.check_seeds([{"seed_key": "profile-x", "topic": "context", "title": "t", "content": "c"}])
