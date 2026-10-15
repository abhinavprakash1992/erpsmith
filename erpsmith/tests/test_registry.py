import ast
import json
import os
import subprocess
import sys
from pathlib import Path

from frappe.tests.utils import FrappeTestCase

from erpsmith import mcp

TOOLS = sorted(t["name"] for t in json.loads(
	(Path(mcp.__file__).with_name("tools.json")).read_text(encoding="utf-8")))
# A locale whose default encoding is ASCII: any text file read without encoding="utf-8" fails on its first non-ASCII byte.
ASCII_LOCALE = {"PYTHONCOERCECLOCALE": "0", "PYTHONUTF8": "0", "LC_ALL": "C"}


def run_python(code, extra_env=None):
	"""Run `code` in a fresh interpreter: import order is process state, so it cannot be tested in this process."""
	r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
		env={**os.environ, **(extra_env or {})})
	return r.returncode, r.stdout.strip(), r.stderr


class TestRegistry(FrappeTestCase):
	def assert_imports(self, code, extra_env=None):
		returncode, out, err = run_python(code, extra_env)
		self.assertEqual(returncode, 0, err)
		return out

	def test_every_import_order_gives_the_full_tool_set(self):
		# importing a tool module first used to run mcp's startup check in the middle of that module's own import
		for first in ("erpsmith.tools.records", "erpsmith.tools.plan", "erpsmith.memory", "erpsmith.mcp", "erpsmith.registry"):
			with self.subTest(first=first):
				out = self.assert_imports(
					f"import json, {first}\nfrom erpsmith import mcp\nprint(json.dumps(sorted(mcp.IMPLS)))")
				self.assertEqual(json.loads(out), TOOLS)

	def test_the_registry_imports_no_tool_module_and_not_mcp(self):
		out = self.assert_imports(
			"import sys, erpsmith.registry\n"
			"print(sorted(m for m in sys.modules if m in ('erpsmith.mcp', 'erpsmith.memory', 'erpsmith.tools.plan', 'erpsmith.tools.records')))\n")
		self.assertEqual(out, "[]")

	def test_mcp_re_exports_the_registry_objects(self):
		from erpsmith import registry

		self.assertIs(mcp.SCHEMAS, registry.SCHEMAS)
		self.assertIs(mcp.IMPLS, registry.IMPLS)
		self.assertIs(mcp.tool, registry.tool)

	def test_tool_refuses_a_name_without_a_schema(self):
		with self.assertRaises(KeyError):
			mcp.tool("no_such_tool")(lambda: None)

	def test_text_files_are_read_as_utf8_whatever_the_locale(self):
		# instructions.md and the embedding fixture hold non-ASCII text
		out = self.assert_imports(
			"import erpsmith.tests.test_vectors\nfrom erpsmith import mcp\nprint('\\u2014' in mcp.INSTRUCTIONS)",
			ASCII_LOCALE)
		self.assertEqual(out, "True")

	def test_every_text_read_names_its_encoding(self):
		# tools.json is ASCII today, so the locale test cannot see its read; this covers it and any file added later
		bare = []
		for path in Path(mcp.__file__).parent.rglob("*.py"):
			for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
				if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", None)) in ("read_text", "open"):
					if not any(k.arg == "encoding" for k in node.keywords) and not any(
							isinstance(a, ast.Constant) and "b" in str(a.value) for a in node.args):
						bare.append(f"{path.name}:{node.lineno}")
		self.assertEqual(bare, [])
