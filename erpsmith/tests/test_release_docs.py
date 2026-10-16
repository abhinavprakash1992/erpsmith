import re
from pathlib import Path

from frappe.tests.utils import FrappeTestCase

from erpsmith import mcp
from erpsmith.tests.test_tool_descriptions import COMMANDS, SHOUTING

ROOT = Path(__file__).resolve().parents[2]
NOT_AFFILIATED = "Not affiliated with or endorsed by Frappe Technologies"
PRODUCT_NAME = re.compile(r"\b(ERPNext|Frappe)\s*(Smith|Copilot|MCP|AI)\b", re.I)
PRICING = re.compile(r"\bpaid\b|\bpro tier\b|\blicen[cs]e key\b", re.I)


def read(path):
	return (ROOT / path).read_text(encoding="utf-8")


def public_docs():
	files = ["README.md", "PRIVACY.md", *sorted(str(p.relative_to(ROOT)) for d in ("docs/listing", "docs/launch") for p in (ROOT / d).glob("*.md"))]
	return {f: read(f) for f in files}


class TestReleaseDocs(FrappeTestCase):
	def test_the_marketplace_short_description_is_40_to_80_characters(self):
		text = read("docs/listing/frappe-marketplace.md")
		short = text.split("## Short description", 1)[1].strip().splitlines()[0].strip()
		self.assertTrue(40 <= len(short) <= 80, f"{len(short)}: {short}")

	def test_the_public_pages_say_they_are_not_affiliated(self):
		for f in ("README.md", "PRIVACY.md", "docs/listing/frappe-marketplace.md", "docs/listing/claude-directory.md", "docs/launch/forum-post.md"):
			self.assertIn(NOT_AFFILIATED, read(f), f)

	def test_no_trademarked_product_name_and_no_pricing_words(self):
		for f, text in public_docs().items():
			self.assertIsNone(PRODUCT_NAME.search(text), f"{f}: {PRODUCT_NAME.search(text)}")
			self.assertIsNone(PRICING.search(text), f"{f}: {PRICING.search(text)}")

	def test_the_privacy_policy_covers_what_the_spec_asks(self):
		text = read("PRIVACY.md")
		headings = re.findall(r"^#+ (.*)$", text, re.M)
		for topic in ("data collected", "how it is used and stored", "third-party sharing", "retention", "contact"):
			self.assertTrue(any(topic in h.lower() for h in headings), f"no heading for {topic}: {headings}")
		for needle in ("docs.erpnext.com", "plaintext", "System Manager", "abhinav.prakash1992@gmail.com"):
			self.assertIn(needle, text)

	def test_the_readme_has_the_onboarding_prompt(self):
		text = read("README.md")
		section = text.split("## Teach ERPSmith how your company works", 1)[1].split("\n## ", 1)[0]
		for needle in ("add_memory", 'topic="context"', "third-person"):
			self.assertIn(needle, section)

	def test_the_readme_has_the_sections_the_spec_asks_for(self):
		text = read("README.md")
		for needle in ("bench get-app", "install-app", "server_script_enabled", "PRIVACY.md", "Abhinav Prakash", "MIT"):
			self.assertIn(needle, text)

	def test_the_directory_listing_names_all_59_tools_and_is_descriptive(self):
		text = read("docs/listing/claude-directory.md")
		self.assertEqual(len(mcp.SCHEMAS), 59)
		for name in mcp.SCHEMAS:
			self.assertRegex(text, rf"`{name}`", name)
		self.assertIsNone(SHOUTING.search(text), SHOUTING.search(text))
		self.assertIsNone(COMMANDS.search(text), COMMANDS.search(text))

	def test_the_release_files_exist(self):
		for f in ("docs/launch/awesome-frappe.md", "docs/launch/demo-script.md", "docs/release/owner-checklist.md"):
			self.assertTrue((ROOT / f).read_text(encoding="utf-8").strip(), f)
