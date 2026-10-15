"""after_install and after_migrate: put the seed notes in place.

Both hooks run again on every migrate, and neither may stop an install or a migrate.
"""

import frappe

from erpsmith import memory


def after_install():
	_setup()


def after_migrate():
	_setup()


def _setup():
	try:
		memory.load_seeds()
	except Exception:
		frappe.log_error(title="ERPSmith: loading the seed memory failed")
