"""Checks server-side Python against what Frappe's safe_exec sandbox really runs, before the script is saved.

Frappe saves a Server Script after a plain Python syntax check, so a script the sandbox rejects only fails later,
when its event fires. Each rule below was observed in safe_exec on Frappe v15 and v16, and
erpsmith/tests/test_sanitizer.py runs every case through the real sandbox: a rule that stops being true fails a test.
"""

import ast

from erpsmith.registry import InvalidArguments

GLOBAL_MODULES = {"frappe", "json"}  # already globals in the sandbox, so `import frappe` / `import json` can go
# Names the sandbox puts in a script's local scope, by script type. Functions cannot see them (see _scope_problems).
INJECTED = {"DocType Event": {"doc"}, "Permission Query": {"user", "conditions"}, "Script Report": {"filters", "data", "result"}}
MISSING_BUILTINS = {
	"hasattr": "read the attribute directly (a missing one reads as None) or use dict.get",
	"getattr": "read the attribute directly (a missing one reads as None) or use dict.get",
	"type": "use isinstance(x, str) (or the type you expect) to test a value",
}
MISSING_ATTRIBUTES = {
	"frappe.get_roles": 'frappe.db.exists("Has Role", {"parent": frappe.session.user, "role": "<role>"})',
	"frappe.set_user": "not available; frappe.session.user reads the current user",
	"frappe.logger": "frappe.log_error(title, message)",
	"frappe.publish_realtime": "not available; frappe.msgprint shows a message to the user",
	"frappe.generate_hash": "frappe.utils.generate_hash()",
	"frappe.local": "not available (it reads as None); frappe.session, frappe.form_dict and frappe.flags cover most uses",
	"frappe.utils.background_jobs": "frappe.enqueue(...)",
}
WRITE_SQL = {"insert", "update", "delete", "replace", "create", "alter", "drop", "truncate", "rename"}
DATE_HELPERS = "frappe.utils has the date helpers (getdate, get_datetime, add_days, now_datetime, date_diff)"


class UnsafeScript(InvalidArguments):
	"""A script that Frappe's sandbox would reject; the message lists each problem with its replacement."""


def _dotted(node):
	parts = []
	while isinstance(node, ast.Attribute):
		parts.append(node.attr)
		node = node.value
	if isinstance(node, ast.Name):
		parts.append(node.id)
		return ".".join(reversed(parts))
	return None


def _import_problem(node):
	if isinstance(node, ast.ImportFrom):
		module = node.module or ""
		if module.split(".")[0] == "frappe":
			calls = ", ".join(f"{module}.{a.name}" for a in node.names)
			return f"imports raise ImportError in the sandbox; frappe is already available, so call {calls} directly"
		if module.split(".")[0] == "datetime":
			return f"imports raise ImportError in the sandbox and datetime is not available; {DATE_HELPERS}"
		return f"imports raise ImportError in the sandbox, and {module} is not available"
	names = [a.name for a in node.names]
	if any(n.split(".")[0] == "datetime" for n in names):
		return f"imports raise ImportError in the sandbox and datetime is not available; {DATE_HELPERS}"
	if all(n.split(".")[0] in GLOBAL_MODULES for n in names):
		return "imports raise ImportError in the sandbox; frappe and json are already available under their own names"
	return f"imports raise ImportError in the sandbox, and {', '.join(names)} is not available; only frappe, json and the safe builtins are"


def _bound(nodes):
	"""Names bound by these statements, not looking inside function, lambda or class bodies."""
	found, stack = set(), list(nodes)
	while stack:
		node = stack.pop()
		if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
			found.add(node.name)
			continue
		if isinstance(node, ast.Lambda):
			continue
		if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
			found.add(node.id)
		stack.extend(ast.iter_child_nodes(node))
	return found


def _top_level_functions(tree):
	found, stack = [], list(tree.body)
	while stack:
		node = stack.pop()
		if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
			found.append(node)
		elif not isinstance(node, (ast.Lambda, ast.ClassDef)):
			stack.extend(ast.iter_child_nodes(node))
	return found


def _scope_problems(tree, injected):
	"""safe_exec runs a script with separate globals and locals, so a function body cannot see names the script
	defines at top level (other functions, variables) or names the sandbox injects (doc): they raise NameError."""
	outer = _bound(tree.body) | injected
	problems = []
	for fn in _top_level_functions(tree):
		inner = {a.arg for a in ast.walk(fn.args) if isinstance(a, ast.arg)}
		for node in ast.walk(fn):
			if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
				inner.add(node.id)
			elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not fn:
				inner.add(node.name)
			elif isinstance(node, ast.Global):
				inner.update(node.names)
		for node in ast.walk(fn):
			if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in outer and node.id not in inner:
				problems.append((node.lineno, f"function {fn.name} uses {node.id}, but a function cannot see names from outside "
					f"it in the sandbox; pass {node.id} in as an argument or define it inside {fn.name}"))
	return problems


def check(script, script_type=None):
	"""(clean script, removed import lines, problems). Problems are 'Line N: ...' strings; none means the script is fine."""
	try:
		tree = ast.parse(script)
	except SyntaxError as e:
		return script, [], [f"Line {e.lineno}: Python syntax error: {e.msg}"]
	lines = script.split("\n")
	removed, found = {}, []
	# names the script binds itself (assignment, def, parameter) shadow the builtins it lacks: `type = 1` then `x = type` is fine
	own = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)} \
		| {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))} \
		| {n.arg for n in ast.walk(tree) if isinstance(n, ast.arg)}

	def flag(node, text):
		found.append((node.lineno, text))

	for node in ast.walk(tree):
		if isinstance(node, ast.Import) and all(a.asname is None and a.name.split(".")[0] in GLOBAL_MODULES for a in node.names) \
				and node.lineno == node.end_lineno and ";" not in lines[node.lineno - 1] \
				and lines[node.lineno - 1].lstrip().startswith("import "):
			removed[node.lineno] = lines[node.lineno - 1].strip()
		elif isinstance(node, (ast.Import, ast.ImportFrom)):
			flag(node, _import_problem(node))
		elif isinstance(node, ast.ClassDef):
			flag(node, "class definitions fail in the sandbox (NameError: __metaclass__); use functions and dicts")
		elif isinstance(node, ast.Assign) and any(isinstance(t, (ast.Tuple, ast.List)) for t in node.targets):
			flag(node, "unpacking in an assignment (a, b = ...) raises NameError in the sandbox; assign the result to one "
				"name and index it (pair = f(); a = pair[0]). Unpacking in a for loop works.")
		elif isinstance(node, ast.Name):
			if node.id in MISSING_BUILTINS and isinstance(node.ctx, ast.Load) and node.id not in own:
				flag(node, f"{node.id} is not available in the sandbox; {MISSING_BUILTINS[node.id]}")
			elif node.id.startswith("_") and node.id != "_":
				flag(node, f'the sandbox rejects names that start with "_" ({node.id}); rename it')
		elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("_"):
			flag(node, f'the sandbox rejects names that start with "_" ({node.name}); rename it')
		elif isinstance(node, ast.arg) and node.arg.startswith("_"):
			flag(node, f'the sandbox rejects names that start with "_" ({node.arg}); rename it')
		elif isinstance(node, ast.Attribute):
			dotted = _dotted(node)
			if node.attr == "format":
				flag(node, "str.format is blocked by the sandbox; use an f-string")
			elif node.attr.startswith("_"):
				flag(node, f'the sandbox rejects attribute names that start with "_" (.{node.attr})')
			elif dotted in ("frappe.db.commit", "frappe.db.rollback") and script_type == "DocType Event":
				flag(node, f"{dotted} is not available in DocType Event scripts; Frappe commits at the end of the request")
			else:
				for name, fix in MISSING_ATTRIBUTES.items():
					if dotted == name or (dotted or "").startswith(name + "."):
						flag(node, f"{name} is not available in the sandbox; {fix}")
						break
		elif isinstance(node, ast.Call) and _dotted(node.func) == "frappe.db.sql" and node.args \
				and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
			words = node.args[0].value.split()
			if words and words[0].lower() in WRITE_SQL:
				flag(node, "frappe.db.sql runs read-only queries in the sandbox; write with frappe.db.set_value or "
					"frappe.get_doc(...).save()")
	found += _scope_problems(tree, INJECTED.get(script_type, set()))

	clean = [
		line[:len(line) - len(line.lstrip())] + "pass  # import removed: frappe and json are already available"
		if i in removed else line
		for i, line in enumerate(lines, 1)
	]
	problems = [f"Line {line}: {text}" for line, text in sorted(set(found))]
	return "\n".join(clean), list(removed.values()), problems


def clean_or_raise(script, script_type=None):
	"""(clean script, removed import lines), or UnsafeScript listing every problem."""
	clean, removed, problems = check(script, script_type)
	if problems:
		raise UnsafeScript("The script would fail in Frappe's sandbox:\n" + "\n".join(problems)
			+ "\nRULE 1 describes the sandbox; search_memory can find working patterns.")
	return clean, removed
