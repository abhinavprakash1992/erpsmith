"""Memory: notes about this site (sandbox facts, code patterns, bug fixes, context) that are found by meaning.

A note is one MCP Memory row, shared by every user of the site. The DocType controller embeds
"title + blank line + content" whenever either changes, so search stays right however a note is edited.
"""

import re

import frappe
from frappe.utils import now_datetime

DOCTYPE = "MCP Memory"
TOPICS = ("sandbox-rule", "pattern", "bug-fix", "context", "handoff")
MAX_TITLE_CHARS = 140  # the Data column
MAX_CONTENT_CHARS = 20_000  # the longest note in a real 89-note memory is 2,531; embed() reads 512 tokens anyway
MAX_QUERY_CHARS = 4000  # a search query is cut here, not rejected (512 tokens are about 2,000 characters)


def note_text(title, content):
	"""What gets embedded for a note."""
	return f"{title or ''}\n\n{content or ''}"


def make_id(topic, title):
	"""A readable id, `<topic>__<slug>__<yyyymmddhhmmss>` like the Node server's, made unique with -2, -3 ..."""
	slug = re.sub(r"[^a-z0-9]+", "-", (title or "untitled").lower()).strip("-")[:50]
	base = f"{topic}__{slug}__{now_datetime().strftime('%Y%m%d%H%M%S')}"
	name, n = base, 1
	while frappe.db.exists(DOCTYPE, name):
		n += 1
		name = f"{base}-{n}"
	return name
