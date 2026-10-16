"""Redact secrets from script code before it is indexed (addendum 2.3)."""

import re

REDACTED = "[redacted]"
_NAMES = r"pass(?:word)?|secret|token|api[_-]?key|auth|bearer|private[_-]?key|client[_-]?secret|webhook|signature"

# ponytail: regex scrubber, best effort, not a guarantee. A missed shape is a new test case plus one regex.
# Order matters: whole blocks first, name-keyed values next, bare shapes last.
_RULES = [(re.compile(p, f), r) for p, f, r in (
	(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S, REDACTED),
	(r"data:[\w.+-]+/[\w.+-]+[^\"'\s)]*", 0, "[data URI]"),
	# name, optional closing quote of a dict key, `=` or `:`, then a quoted value
	(rf"""((?:[\w-]*(?:{_NAMES})[\w-]*)["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*')""", re.I, rf'\1"{REDACTED}"'),
	(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{4,}", 0, rf"\1 {REDACTED}"),
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
