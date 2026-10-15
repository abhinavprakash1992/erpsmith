"""Rules for the text of seed notes (spec section 8: facts and recipes only, nothing instance-specific).

Standard library only, so the dev/ scripts and the tests share one definition.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWED_HOSTS = frozenset({"docs.frappe.io", "frappeframework.com", "docs.erpnext.com", "erpnext.com", "frappe.io", "github.com"})
URL = re.compile(r"https?://([^/\s)>\]\"'`:]+)", re.I)
# The directory review rejects servers that make Claude pull behavioural instructions from stored text,
# so a seed note states what Frappe does; it never tells the assistant what to do.
DIRECTIVE = re.compile(
	r"\b(always|never|must|do not|don't|should not|shouldn't|stop after|mandatory|make sure|ensure|required to)\b", re.I)
IDENTIFIERS = {
	"customer-name": re.compile(r"edif[y]|kuzaga[n]", re.I),
	"email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
	"gstin": re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d]\b"),
	"long-number": re.compile(r"\b\d{9,}\b"),
	"ip-address": re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),
}
IDENTIFIER_RULES = frozenset({*IDENTIFIERS, "url", "blocklisted-term"})
SOFT = {
	"rupee-amount": re.compile(r"₹\s?\d"),
	"dated": re.compile(r"\b20\d\d-\d\d(?:-\d\d)?\b"),
	"names-the-mcp-tooling": re.compile(r"\bMCP\b|sanitizer", re.I),
}
LONG_CONTENT = 1500
CONTEXT_TOPICS = frozenset({"context", "handoff"})


def _allowed_host(host):
	host = host.lower()
	return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def hard_violations(text, extra_terms=()):
	"""Names of the rules `text` breaks. A shipped seed note may break none of them."""
	found = [name for name, rx in IDENTIFIERS.items() if rx.search(text)]
	if not all(_allowed_host(host) for host in URL.findall(text)):
		found.append("url")
	lowered = text.lower()
	if any(term.strip() and term.strip().lower() in lowered for term in extra_terms):
		found.append("blocklisted-term")
	if DIRECTIVE.search(text):
		found.append("directive")
	return found


def soft_flags(text, topic, content_length):
	"""Things the owner should look at, but that do not by themselves make a note unshippable."""
	flags = [name for name, rx in SOFT.items() if rx.search(text)]
	if topic in CONTEXT_TOPICS:
		flags.append("topic-context")
	if content_length > LONG_CONTENT:
		flags.append("long")
	return flags


def private_blocklist(path=None):
	"""Customer-specific terms from private/blocklist.txt (one per line, # comments). Never committed."""
	path = Path(path) if path else ROOT / "private" / "blocklist.txt"
	if not path.exists():
		return []
	lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
	return [line for line in lines if line and not line.startswith("#")]
