import os
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from erpsmith.tests.helpers import call, drop_doctype, forget_plans, run

GADGET = "ERPSmith Test Gadget"
RENAMED = "ERPSmith Test Gizmo"
PART = "ERPSmith Test Gadget Part"
SETTINGS = "ERPSmith Test Settings"
FIELDS = [
	{"label": "Title", "fieldtype": "Data", "required": True, "inListView": True},
	{"label": "Quantity", "fieldtype": "Int"},
	{"label": "Status", "fieldtype": "Select", "options": "Open\nClosed", "default": "Open"},
]


class TestDocTypeTools(FrappeTestCase):
	def tearDown(self):
		for name in (GADGET, RENAMED, PART, SETTINGS):
			drop_doctype(name)
		forget_plans()

	def test_create_get_list_and_delete_a_custom_doctype(self):
		err, out = run("create_doctype", {"name": GADGET, "module": "Custom", "fields": FIELDS})
		self.assertFalse(err, out)
		self.assertEqual(out["fields"], ["naming_series", "title", "quantity", "status"])
		self.assertEqual(out["autoname"], "naming_series:")

		err, got = call("get_doctype", {"name": GADGET})
		self.assertFalse(err, got)
		self.assertTrue(got["custom"])
		title = next(f for f in got["fields"] if f["fieldname"] == "title")
		self.assertEqual((title["reqd"], title["in_list_view"]), (1, 1))
		self.assertEqual(got["permissionSource"], "DocPerm")
		self.assertEqual([(p["role"], p["write"]) for p in got["permissions"]], [("System Manager", 1)])

		err, listed = call("list_doctypes", {"search": "ERPSmith Test G"})
		self.assertFalse(err, listed)
		self.assertIn(GADGET, [d["name"] for d in listed["doctypes"]])

		err, rec = run("create_record", {"doctype": GADGET, "data": {"title": "first"}})
		self.assertFalse(err, rec)
		self.assertTrue(rec["name"].startswith("CUSTOM-"), rec["name"])

		err, out = run("delete_doctype", {"name": GADGET})
		self.assertFalse(err, out)
		self.assertFalse(frappe.db.exists("DocType", GADGET))
		self.assertIn("trim-database", out["table"])

	def test_rename_moves_the_doctype_and_its_table(self):
		self.assertFalse(run("create_doctype", {"name": GADGET, "module": "Custom", "fields": FIELDS})[0])
		err, out = run("rename_doctype", {"oldName": GADGET, "newName": RENAMED})
		self.assertFalse(err, out)
		self.assertEqual(out["to"], RENAMED)
		self.assertTrue(frappe.db.exists("DocType", RENAMED))
		self.assertTrue(frappe.db.table_exists(RENAMED))
		self.assertFalse(frappe.db.exists("DocType", GADGET))

	def test_child_single_and_submittable_doctypes(self):
		err, out = run("create_doctype", {"name": PART, "module": "Custom", "isChild": True, "fields": FIELDS[:1]})
		self.assertFalse(err, out)
		self.assertEqual(out["fields"], ["title"])
		meta = frappe.get_meta(PART)
		self.assertEqual((meta.istable, len(meta.permissions)), (1, 0))

		err, out = run("create_doctype", {"name": SETTINGS, "module": "Custom", "isSingle": True, "fields": FIELDS[:1]})
		self.assertFalse(err, out)
		self.assertEqual(frappe.get_meta(SETTINGS).issingle, 1)

		err, out = run("create_doctype", {"name": GADGET, "module": "Custom", "isSubmittable": True, "fields": FIELDS[:1]})
		self.assertFalse(err, out)
		perm = frappe.get_meta(GADGET).permissions[0]
		self.assertEqual((perm.submit, perm.cancel, perm.amend), (1, 1, 1))

	def test_bad_field_specs_are_invalid_arguments(self):
		for fields in ("title", [{"fieldtype": "Data"}], ["title"]):
			err, out = run("create_doctype", {"name": GADGET, "module": "Custom", "fields": fields})
			self.assertTrue(err, fields)
			self.assertEqual(out["type"], "InvalidArguments", out)
		self.assertFalse(frappe.db.exists("DocType", GADGET))

	def test_standard_doctypes_are_never_deleted_or_renamed(self):
		# frappe.delete_doc / rename_doc are mocked: on a developer-mode site the real calls would remove ToDo's source files
		todo_json = frappe.get_app_path("frappe", "desk", "doctype", "todo", "todo.json")
		with patch("frappe.delete_doc") as delete_doc, patch("frappe.rename_doc") as rename_doc:
			err, out = run("delete_doctype", {"name": "ToDo"})
			self.assertTrue(err)
			self.assertEqual(out["type"], "StandardDocType")
			self.assertIn("add_field", out["message"])
			self.assertIn("upgrade-safe", out["suggestion"])
			err, out = run("rename_doctype", {"oldName": "Note", "newName": "ERPSmith Test Note"})
			self.assertTrue(err)
			self.assertEqual(out["type"], "StandardDocType")
		delete_doc.assert_not_called()
		rename_doc.assert_not_called()
		self.assertTrue(os.path.exists(todo_json))

	def test_unknown_doctypes_are_not_found(self):
		err, out = call("get_doctype", {"name": "ERPSmith Test Nothing"})
		self.assertTrue(err)
		self.assertEqual(out["type"], "DoesNotExistError")
		err, out = run("delete_doctype", {"name": "ERPSmith Test Nothing"})
		self.assertEqual(out["type"], "DoesNotExistError")
