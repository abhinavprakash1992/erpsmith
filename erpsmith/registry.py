"""The tool registry: schemas from tools.json, implementations registered with @tool.

This module imports no tool module and not erpsmith.mcp, so a tool module can import `tool`
from here in any import order. erpsmith.mcp imports the tool modules and checks that the two halves match.
"""

import json
from pathlib import Path

SCHEMAS = {t["name"]: t for t in json.loads((Path(__file__).parent / "tools.json").read_text(encoding="utf-8"))}
IMPLS = {}


def tool(name):
	"""Register the implementation of tool `name`; its schema lives in tools.json."""

	def register(fn):
		if name not in SCHEMAS:
			raise KeyError(f"{name} has no schema in tools.json")
		if name in IMPLS:
			raise ValueError(f"{name} already has an implementation")
		IMPLS[name] = fn
		return fn

	return register
