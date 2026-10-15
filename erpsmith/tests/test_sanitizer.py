from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.safe_exec import safe_exec

from erpsmith import sanitizer

# (script, script_type). Each one fails in Frappe's real sandbox, and the sanitizer must flag it.
FAILS = [
	("x = hasattr(frappe, 'db')", None),
	("x = getattr(frappe, 'db')", None),
	("x = frappe.get_roles()", None),
	("frappe.set_user('Administrator')", None),
	("frappe.logger().info('x')", None),
	("frappe.publish_realtime('x', {})", None),
	("x = frappe.generate_hash()", None),
	("frappe.utils.background_jobs.enqueue('frappe.ping')", None),
	("x = '{}'.format(1)", None),
	("x = frappe.format(1)", None),  # every attribute named format is rejected at compile time, frappe.format included
	("_x = 1", None),
	("def _f():\n    return 1\nx = _f()", None),
	("def f(_a):\n    return _a\nx = f(1)", None),
	("x = frappe._dict()", None),
	("def f():\n    return 1, 2\na, b = f()", None),
	("class A:\n    pass", None),
	("def g():\n    return 1\ndef f():\n    return g()\nx = f()", None),
	("Y = 5\ndef f():\n    return Y\nx = f()", None),
	("h = lambda: 1\ndef f():\n    return h()\nx = f()", None),
	("def f(n):\n    return f(n - 1) if n else 0\nx = f(2)", None),
	("def f():\n    return doc.name\nx = f()", "DocType Event"),
	("def f():\n    return filters.get('company')\nresult = f()", "Script Report"),
	("frappe.db.sql('update `tabToDo` set description=description where 1=0')", None),
	("frappe.db.commit()", "DocType Event"),
	("import datetime\nx = datetime.date.today()", None),
	("from frappe.utils import getdate\nx = getdate()", None),
	("import json as j\nx = j.dumps({})", None),
	("import os", None),
	("def f(:\n    pass", None),
]
# Each one runs in the sandbox (after the sanitizer's own clean-up), and the sanitizer must accept it.
WORKS = [
	("x = isinstance(1, int)", None),
	("x = f'{1}'", None),
	("x = frappe.get_hooks('app_name')", None),
	("for a, b in [(1, 2)]:\n    x = a + b", None),
	("x = [k for k, v in {'a': 1}.items()]", None),
	("def f():\n    def g():\n        return 1\n    return g()\nx = f()", None),
	("def f(n):\n    return n + 1\nx = f(1)", None),
	("Y = 1\ndef f():\n    global Y\n    Y = 2\nf()", None),
	("try:\n    frappe.throw('x')\nexcept Exception as e:\n    x = str(e)", None),
	("x = frappe.db.sql('select 1')", None),
	("x = _('Hello')", None),
	("x = frappe.utils.generate_hash()", None),
	("x = frappe.utils.getdate('2026-01-01')", None),
	("x = doc.name", "DocType Event"),
	("import json\nx = json.dumps({'a': 1})", None),
	("def f():\n    import frappe\n    return frappe.session.user\nx = f()", None),
	("print('x')", None),
]
LOCALS = {"DocType Event": lambda: {"doc": frappe._dict(name="DOC-1")},
	"Script Report": lambda: {"filters": frappe._dict(), "data": None, "result": None}}


def sandbox(script, script_type):
	"""Run `script` the way Frappe runs that script type. Returns its local scope."""
	scope = LOCALS.get(script_type, dict)()
	with patch("frappe.utils.safe_exec.is_safe_exec_enabled", return_value=True):
		safe_exec(script, None, scope, restrict_commit_rollback=script_type == "DocType Event")
	return scope


class TestSanitizerAgainstTheRealSandbox(FrappeTestCase):
	def test_every_flagged_case_really_fails_in_the_sandbox(self):
		for script, script_type in FAILS:
			with self.subTest(script=script):
				with self.assertRaises(Exception):
					sandbox(script, script_type)
				self.assertTrue(sanitizer.check(script, script_type)[2], "the sanitizer missed it")

	def test_every_accepted_case_really_runs_in_the_sandbox(self):
		for script, script_type in WORKS:
			with self.subTest(script=script):
				clean, _, problems = sanitizer.check(script, script_type)
				self.assertEqual(problems, [])
				sandbox(clean, script_type)

	def test_frappe_local_reads_as_none_so_it_is_flagged(self):
		self.assertIsNone(sandbox("x = frappe.local.site", None)["x"])
		self.assertEqual(len(sanitizer.check("x = frappe.local.site")[2]), 1)  # one problem, not one per attribute


class TestSanitizerOutput(FrappeTestCase):
	def test_import_of_a_global_module_is_replaced_by_pass_keeping_line_numbers(self):
		clean, removed, problems = sanitizer.check("import json\nimport frappe, json\nx = 1")
		self.assertEqual(problems, [])
		self.assertEqual(removed, ["import json", "import frappe, json"])
		self.assertEqual(clean.split("\n")[2], "x = 1")
		self.assertTrue(clean.startswith("pass  # import removed"))

	def test_problems_name_the_line_and_the_replacement_sorted_by_line(self):
		_, _, problems = sanitizer.check("x = 1\ny = frappe.get_roles()\nimport datetime")
		self.assertEqual(len(problems), 2)
		self.assertTrue(problems[0].startswith("Line 2: frappe.get_roles"))
		self.assertIn("Has Role", problems[0])
		self.assertTrue(problems[1].startswith("Line 3:"))
		self.assertIn("frappe.utils", problems[1])

	def test_from_frappe_imports_name_the_direct_call(self):
		problems = sanitizer.check("from frappe.utils import getdate, add_days")[2]
		self.assertIn("frappe.utils.getdate, frappe.utils.add_days", problems[0])

	def test_commit_is_flagged_only_in_doctype_event_scripts(self):
		self.assertTrue(sanitizer.check("frappe.db.commit()", "DocType Event")[2])
		self.assertEqual(sanitizer.check("frappe.db.commit()", "Scheduler Event")[2], [])

	def test_an_import_that_shares_its_line_with_other_code_is_a_problem_not_a_rewrite(self):
		script = "try: import json\nexcept Exception:\n    pass"
		clean, removed, problems = sanitizer.check(script)
		self.assertEqual(removed, [])
		self.assertEqual(len(problems), 1)
		self.assertEqual(clean, script)

	def test_text_in_strings_and_comments_is_not_code(self):
		script = "# hasattr(x) and '{}'.format\nx = 'call hasattr( or .format( here'"
		self.assertEqual(sanitizer.check(script)[2], [])

	def test_clean_or_raise_lists_every_problem(self):
		with self.assertRaises(sanitizer.UnsafeScript) as caught:
			sanitizer.clean_or_raise("x = hasattr(a, 'b')\nclass A:\n    pass")
		message = str(caught.exception)
		self.assertTrue(message.startswith("The script would fail in Frappe's sandbox:"))
		self.assertIn("Line 1:", message)
		self.assertIn("Line 2:", message)
		self.assertEqual(sanitizer.clean_or_raise("import json\nx = 1"), ("pass  # import removed: frappe and json are already available\nx = 1", ["import json"]))
