"""Redact secrets from script code before it is indexed (addendum 2.3)."""

import re

REDACTED = "[redacted]"
# `auth` stands alone (auth, auth_token, Authorization, oauth) so that author and author_name stay readable
_NAMES = (r"pass(?:word)?|secret|token|api[_-]?key|(?<![A-Za-z0-9])o?auth(?:orization|entication)?(?![A-Za-z])|bearer"
	r"|private[_-]?key|client[_-]?secret|webhook|signature")
# a word (letters, digits, _ and -) that contains a name. Anchored at the word start and tested by lookahead, so the
# cost is linear: `[\w-]*(?:names)[\w-]*` retries from every character and again for every name inside the word.
_NAME = rf"(?<![\w-])(?=[\w-]*(?:{_NAMES}))[\w-]+"
# a Python string: optional prefix (b, r, f, rb ...), triple quotes first, then quotes whose body allows escapes
_STR = r"""[rRbBuUfF]{0,2}(?:\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')"""

# ponytail: regex scrubber, best effort, not a guarantee. A missed shape is a new test case plus one regex.
# Order matters: whole blocks first, name-keyed values next, bare shapes last.
_RULES = [(re.compile(p, f), r) for p, f, r in (
	(r"-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----.*?-----END [A-Z ]*PRIVATE KEY(?: BLOCK)?-----", re.S, REDACTED),
	(r"data:[\w.+-]+/[\w.+-]+[^\"'\s)]*", 0, "[data URI]"),
	# name, optional closing quote and `]` of a dict key or subscript, `=` or `:`, then a string, or a tuple's second string
	(rf"""({_NAME}["']?\]?\s*[:=]\s*(?:\(\s*{_STR}\s*,\s*)?){_STR}""", re.I, rf'\1"{REDACTED}"'),
	# a quoted name, a comma, then a string: set_value(dt, n, "password", "x"), doc.set("api_key", "x")
	(rf"""(["']{_NAME}["']\s*,\s*){_STR}""", re.I, rf'\1"{REDACTED}"'),
	# the token has a digit or symbol, mixes lower and upper case, or is 16+ characters: "Basic information" is prose
	(r"\b(Bearer|Basic)\s+(?:(?=[A-Za-z0-9._~+/=-]*[\d._~+/=-])[A-Za-z0-9._~+/=-]{4,}|(?=[A-Za-z]*[a-z][A-Z])[A-Za-z]{8,}"
	 r"|[A-Za-z0-9]{16,})", 0, rf"\1 {REDACTED}"),
	(r"((?<!\w)\w+://)[^/\s:@\"']+:[^/\s@\"']+@", 0, rf"\1{REDACTED}@"),
	(r"\bsk-[A-Za-z0-9_-]{20,}|\bgh[pousr]_[A-Za-z0-9]{36,}|\bxox[abp]-[A-Za-z0-9-]+|\bAKIA[0-9A-Z]{16}\b"
	 r"|\beyJ[\w-]+\.[\w-]+\.[\w-]+|\b[Tt]oken\s+[0-9A-Fa-f]{10,}:[0-9A-Fa-f]{10,}", 0, REDACTED),
	(r"""(["'])(?=[A-Za-z0-9_+/=-]*[A-Za-z])(?=[A-Za-z0-9_+/=-]*\d)[A-Za-z0-9_+/=-]{32,}\1""", 0, f'"{REDACTED}"'),
)]


def scrub(code):
	code = code or ""
	for rx, repl in _RULES:
		code = rx.sub(repl, code)
	return code
