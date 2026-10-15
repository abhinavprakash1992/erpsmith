import re

from frappe.tests.utils import FrappeTestCase

import frappe

from erpsmith import guards, mcp
from erpsmith.guards import DangerousOp, GateBlocked
from erpsmith.registry import InvalidArguments

# Spec 6a: a description says what the tool does and when to use it. It does not tell Claude how to behave,
# to call tools the user did not ask for, or to fetch instructions from elsewhere.
SHOUTING = re.compile(r"\b(MANDATORY|MUST|ALWAYS|NEVER|PROACTIVELY|CRITICAL|IMPORTANT)\b")
COMMANDS = re.compile(r"you must|without being asked|call this before|always call|do not wait", re.I)


class TestToolDescriptions(FrappeTestCase):
	def test_every_description_is_descriptive_not_commanding(self):
		for name, schema in mcp.SCHEMAS.items():
			text = schema["description"]
			self.assertGreaterEqual(len(text), 40, name)
			self.assertIsNone(SHOUTING.search(text), f"{name}: {SHOUTING.search(text)}")
			self.assertIsNone(COMMANDS.search(text), f"{name}: {COMMANDS.search(text)}")

	def test_names_and_titles_follow_the_directory_rules(self):
		for name, schema in mcp.SCHEMAS.items():
			self.assertLessEqual(len(name), 64, name)
			self.assertTrue(schema["title"].strip(), name)

	def test_memory_and_knowledge_tools_say_who_may_use_them(self):
		for name in ("search_memory", "add_memory", "update_memory", "delete_memory", "list_memory", "dump_memory",
				"search_erpnext_knowledge"):
			self.assertIn("System Manager", mcp.SCHEMAS[name]["description"], name)

	def test_every_property_description_is_descriptive_too(self):
		def descriptions(node, path):
			if isinstance(node, dict):
				if isinstance(node.get("description"), str) and path:
					yield path, node["description"]
				for key, child in node.items():
					yield from descriptions(child, f"{path}.{key}")
			elif isinstance(node, list):
				for i, child in enumerate(node):
					yield from descriptions(child, f"{path}[{i}]")

		for name, schema in mcp.SCHEMAS.items():
			for path, text in descriptions(schema["inputSchema"], name):
				self.assertIsNone(SHOUTING.search(text), f"{path}: {text}")
				self.assertIsNone(COMMANDS.search(text), f"{path}: {text}")

	def test_error_suggestions_and_the_plan_next_step_are_descriptive(self):
		samples = [GateBlocked("BLOCKED by plan gate: x"), DangerousOp("DANGEROUS OP BLOCKED x"), LookupError("Unknown tool: x"),
			InvalidArguments("x"), frappe.PermissionError("x"), frappe.DoesNotExistError("x"),
			frappe.DuplicateEntryError("x"), frappe.LinkValidationError("x"), frappe.ValidationError("x"),
			ImportError("__import__ not found"), frappe.PermissionError("x is not whitelisted"), TypeError("x"), KeyError("x")]
		texts = [mcp.suggestion(e) for e in samples]
		texts.append(guards.submit_plan("lint", [{"tool": "create_record", "args": {}}])["next_step"])
		for text in texts:
			self.assertIsNone(SHOUTING.search(text), text)
			self.assertIsNone(COMMANDS.search(text), text)
			self.assertNotRegex(text, r"(^|\. )(Call|Show|Wait|Use|Remove|Check|Do not|Don't) ", text)
		self.assertNotIn("call_method", mcp.suggestion(KeyError("x")))  # an unknown error does not point at the broadest write tool
