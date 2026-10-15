"""The tool registry: schemas from tools.json, implementations registered with @tool, and what a tool's name says about it.

This module imports no tool module and not erpsmith.mcp, so a tool module can import from here in any import order.
erpsmith.mcp imports the tool modules and checks that the two halves match.
"""

import json
from pathlib import Path

SCHEMAS = {t["name"]: t for t in json.loads((Path(__file__).parent / "tools.json").read_text(encoding="utf-8"))}
IMPLS = {}

# Spec §4: annotations come from the tool name. Every tool name must match one rule (hints() raises otherwise),
# and the plan gate reads the same rules, so a tool cannot be listed as read-only and gated as a write, or the reverse.
READ_ONLY_PREFIXES = ("get_", "list_", "query_", "search_", "debug_")
READ_ONLY_EXACT = {"full_health_check", "dump_memory", "call_method_read"}
DESTRUCTIVE_PREFIXES = ("delete_", "remove_", "update_", "set_", "fix_", "apply_", "rename_", "reorder_", "activate_")
DESTRUCTIVE_EXACT = {"call_method"}
ADDITIVE_PREFIXES = ("create_", "add_")
ADDITIVE_EXACT = {"submit_plan", "approve_plan"}


class InvalidArguments(ValueError):
	"""A tool argument is missing or out of range. mcp.suggestion() answers it with a pointer to the tool's input schema."""


def hints(name):
	"""readOnlyHint and destructiveHint for tool `name`. KeyError for a name that no rule covers."""
	if name in READ_ONLY_EXACT or name.startswith(READ_ONLY_PREFIXES):
		return {"readOnlyHint": True}
	if name in DESTRUCTIVE_EXACT or name.startswith(DESTRUCTIVE_PREFIXES):
		return {"readOnlyHint": False, "destructiveHint": True}
	if name in ADDITIVE_EXACT or name.startswith(ADDITIVE_PREFIXES):
		return {"readOnlyHint": False, "destructiveHint": False}
	raise KeyError(f"{name} matches no annotation rule: give it a known prefix or add it to an *_EXACT set in registry.py")


def is_read_only(name):
	return hints(name)["readOnlyHint"]


def tool(name):
	"""Register the implementation of tool `name`; its schema lives in tools.json."""

	def register(fn):
		if name not in SCHEMAS:
			raise KeyError(f"{name} has no schema in tools.json")
		old = IMPLS.get(name)
		# A module imported again after a failed first import registers the same functions again: that is
		# allowed, so the next request sees the original error instead of "already has an implementation".
		if old is not None and (old.__module__, old.__qualname__) != (fn.__module__, fn.__qualname__):
			raise ValueError(f"{name} already has an implementation")
		IMPLS[name] = fn
		return fn

	return register


def required_text(name, value):
	if not isinstance(value, str) or not value.strip():
		raise InvalidArguments(f"{name} is required (a non-empty string)")
	return value


def clamp(name, value, default, low, high):
	"""An int from a tool argument: `default` for None, otherwise clamped to low..high (floats are truncated)."""
	if value is None:
		return default
	try:
		number = float(value)
	except OverflowError:  # an int too big for a float, such as 10**400
		return high if value > 0 else low
	except (TypeError, ValueError):
		raise InvalidArguments(f"{name} must be a number") from None
	if isinstance(value, bool) or number != number:  # True is not a count; NaN is not a number
		raise InvalidArguments(f"{name} must be a number")
	return int(max(low, min(high, number)))
