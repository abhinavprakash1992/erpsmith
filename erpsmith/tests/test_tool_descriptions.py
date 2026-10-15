import re

from frappe.tests.utils import FrappeTestCase

from erpsmith import mcp

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
