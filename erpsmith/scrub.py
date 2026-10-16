"""Redact secrets from script code before it is indexed (addendum 2.3)."""

import re

REDACTED = "[redacted]"
# `auth` stands alone (auth, auth_token, Authorization, oauth) so that author and author_name stay readable
_NAMES = (r"pass(?:word)?|secret|token|api[_-]?key|(?<![A-Za-z0-9])o?auth(?:orization|entication)?(?![A-Za-z])|bearer"
	r"|private[_-]?key|client[_-]?secret|webhook|signature")
_NAME = rf"[\w-]*(?:{_NAMES})[\w-]*"
# a Python string: optional prefix (b, r, f, rb ...), triple quotes first, then quotes whose body allows escapes
_STR = r"""[rRbBuUfF]{0,2}(?:\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')"""

# ponytail: regex scrubber, best effort, not a guarantee. A missed shape is a new test case plus one regex.
# Order matters: whole blocks first, name-keyed values next, bare shapes last.
_RULES = [(re.compile(p, f), r) for p, f, r in (
	(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S, REDACTED),
	(r"data:[\w.+-]+/[\w.+-]+[^\"'\s)]*", 0, "[data URI]"),
	# name, optional closing quote of a dict key, `=` or `:`, then a string
	(rf"""({_NAME}["']?\s*[:=]\s*){_STR}""", re.I, rf'\1"{REDACTED}"'),
	# a quoted name, a comma, then a string: set_value(dt, n, "password", "x"), doc.set("api_key", "x")
	(rf"""(["']{_NAME}["']\s*,\s*){_STR}""", re.I, rf'\1"{REDACTED}"'),
	# the token has a digit or symbol, mixes lower and upper case, or is 16+ characters: "Basic information" is prose
	(r"\b(Bearer|Basic)\s+(?:(?=[A-Za-z0-9._~+/=-]*[\d._~+/=-])[A-Za-z0-9._~+/=-]{4,}|(?=[A-Za-z]*[a-z][A-Z])[A-Za-z]{8,}"
	 r"|[A-Za-z0-9]{16,})", 0, rf"\1 {REDACTED}"),
	(r"(\w+://)[^/\s:@\"']+:[^/\s@\"']+@", 0, rf"\1{REDACTED}@"),
	(r"\bsk-[A-Za-z0-9_-]{20,}|\bgh[pousr]_[A-Za-z0-9]{36,}|\bxox[abp]-[A-Za-z0-9-]+|\bAKIA[0-9A-Z]{16}\b"
	 r"|\beyJ[\w-]+\.[\w-]+\.[\w-]+", 0, REDACTED),
	(r"""(["'])(?=[A-Za-z0-9_+/=-]*[A-Za-z])(?=[A-Za-z0-9_+/=-]*\d)[A-Za-z0-9_+/=-]{32,}\1""", 0, f'"{REDACTED}"'),
)]


def scrub(code):
	code = code or ""
	for rx, repl in _RULES:
		code = rx.sub(repl, code)
	return code
