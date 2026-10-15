import frappe
from frappe.tests.utils import FrappeTestCase


class TestSmoke(FrappeTestCase):
	def test_app_is_installed(self):
		self.assertIn("erpsmith", frappe.get_installed_apps())
