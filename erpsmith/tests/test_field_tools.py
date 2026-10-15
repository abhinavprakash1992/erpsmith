import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from erpsmith.tests.helpers import call, drop_doctype, forget_plans, run

GADGET = "ERPSmith Test Gadget"
NOTES = "erpsmith_test_notes"


def make_gadget():
	frappe.get_doc({"doctype": "DocType", "name": GADGET, "module": "Custom", "custom": 1, "autoname": "hash",
		"fields": [{"fieldname": f, "label": f.title(), "fieldtype": "Data"} for f in ("title", "status", "notes")],
		"permissions": [{"role": "System Manager", "read": 1, "write": 1, "create": 1}]}).insert()


class TestFieldTools(FrappeTestCase):
	def setUp(self):
		self.started = now_datetime()
		make_gadget()

	def tearDown(self):
		drop_doctype(GADGET)
		if frappe.db.exists("Custom Field", {"dt": "ToDo", "fieldname": NOTES}):
			frappe.delete_doc("Custom Field", frappe.db.get_value("Custom Field", {"dt": "ToDo", "fieldname": NOTES}))
		frappe.db.sql_ddl(f"alter table `tabToDo` drop column if exists `{NOTES}`")  # Frappe keeps a deleted field's column
		frappe.db.delete("Property Setter", {"doc_type": "ToDo", "creation": (">=", self.started)})
		frappe.clear_cache(doctype="ToDo")
		forget_plans()

	def fieldnames(self, doctype):
		frappe.clear_cache(doctype=doctype)
		return [df.fieldname for df in frappe.get_meta(doctype).fields]

	def test_a_standard_doctype_gets_a_custom_field_placed_after_the_last_field(self):
		last = self.fieldnames("ToDo")[-1]
		err, out = run("add_field", {"doctype": "ToDo", "label": "ERPSmith Test Notes", "fieldtype": "Small Text"})
		self.assertFalse(err, out)
		self.assertEqual((out["fieldname"], out["method"]), (NOTES, "Custom Field"))
		self.assertEqual(frappe.db.get_value("Custom Field", {"dt": "ToDo", "fieldname": NOTES}, "insert_after"), last)
		err, listed = call("list_fields", {"doctype": "ToDo"})
		self.assertFalse(err, listed)
		self.assertTrue(next(f for f in listed["fields"] if f["fieldname"] == NOTES)["custom"])

		err, out = run("update_field", {"doctype": "ToDo", "fieldname": NOTES, "updates": {"label": "Notes Two", "bold": 1}})
		self.assertFalse(err, out)
		self.assertEqual(out["method"], "Custom Field")
		self.assertEqual(frappe.get_meta("ToDo").get_field(NOTES).label, "Notes Two")

		err, out = run("remove_field", {"doctype": "ToDo", "fieldname": NOTES})
		self.assertFalse(err, out)
		self.assertNotIn(NOTES, self.fieldnames("ToDo"))

	def test_a_standard_field_changes_through_property_setters_and_is_never_removed(self):
		err, out = run("update_field", {"doctype": "ToDo", "fieldname": "description", "updates": {"bold": 1}})
		self.assertFalse(err, out)
		self.assertEqual(out["method"], "Property Setter")
		frappe.clear_cache(doctype="ToDo")
		self.assertEqual(frappe.get_meta("ToDo").get_field("description").bold, 1)

		err, out = run("remove_field", {"doctype": "ToDo", "fieldname": "description"})
		self.assertTrue(err)
		self.assertEqual(out["type"], "StandardDocType")
		self.assertIn("set_property", out["message"])
		self.assertIn("description", self.fieldnames("ToDo"))

	def test_a_custom_doctype_is_edited_directly(self):
		err, out = run("add_field", {"doctype": GADGET, "label": "Size", "fieldtype": "Int", "insertAfter": "title"})
		self.assertFalse(err, out)
		self.assertEqual(out["method"], "DocType")
		self.assertEqual(self.fieldnames(GADGET), ["title", "size", "status", "notes"])
		self.assertFalse(frappe.db.exists("Custom Field", {"dt": GADGET}))

		err, out = run("update_field", {"doctype": GADGET, "fieldname": "size", "updates": {"reqd": 1}})
		self.assertFalse(err, out)
		self.assertEqual(out["method"], "DocType")
		self.assertEqual(frappe.get_meta(GADGET).get_field("size").reqd, 1)

		err, out = run("remove_field", {"doctype": GADGET, "fieldname": "size"})
		self.assertFalse(err, out)
		self.assertEqual(self.fieldnames(GADGET), ["title", "status", "notes"])

	def test_reorder_puts_the_listed_fields_first(self):
		err, out = run("reorder_fields", {"doctype": GADGET, "fieldOrder": ["notes", "title", "nope"]})
		self.assertFalse(err, out)
		self.assertEqual(out["order"], ["notes", "title", "status"])
		self.assertEqual(out["ignored"], ["nope"])
		self.assertEqual(self.fieldnames(GADGET), ["notes", "title", "status"])

	def test_set_property_replaces_the_previous_value(self):
		for value in (1, 0):
			err, out = run("set_property", {"doctype": "ToDo", "fieldname": "description", "property": "bold", "value": value})
			self.assertFalse(err, out)
		rows = frappe.get_all("Property Setter", filters={"doc_type": "ToDo", "field_name": "description", "property": "bold"},
			pluck="value")
		self.assertEqual(rows, ["0"])

		err, out = run("set_property", {"doctype": GADGET, "property": "allow_rename", "value": True})
		self.assertFalse(err, out)
		frappe.clear_cache(doctype=GADGET)
		self.assertEqual(frappe.get_meta(GADGET).allow_rename, 1)

	def test_unknown_fields_and_properties_are_refused_and_write_nothing(self):
		before = frappe.db.count("Property Setter")
		cases = [
			("set_property", {"doctype": "ToDo", "fieldname": "no_such_field", "property": "bold", "value": 1}),
			("set_property", {"doctype": "ToDo", "fieldname": "description", "property": "no_such_property", "value": 1}),
			("update_field", {"doctype": "ToDo", "fieldname": "description", "updates": {}}),
			("update_field", {"doctype": "ToDo", "fieldname": "description", "updates": {"colour": "red"}}),
			("update_field", {"doctype": "ToDo", "fieldname": "description", "updates": {"fieldname": "x"}}),
			("add_field", {"doctype": "ToDo", "label": "X", "fieldtype": "Data", "insertAfter": "no_such_field"}),
			("reorder_fields", {"doctype": GADGET, "fieldOrder": "title"}),
		]
		for tool, args in cases:
			err, out = run(tool, args)
			self.assertTrue(err, (tool, args))
			self.assertEqual(out["type"], "InvalidArguments", (tool, out))
		self.assertEqual(frappe.db.count("Property Setter"), before)
