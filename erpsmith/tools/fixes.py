"""Fix tools: the write half of the debug tools. Each one changes the single thing a debug_* report pointed at."""

import frappe

from erpsmith.registry import InvalidArguments, required_text, tool
from erpsmith.tools.builders import grant
from erpsmith.tools.doctype import change_field


@tool("fix_broken_link")
def fix_broken_link(doctype, fieldname, correctDoctype):
	if not frappe.db.exists("DocType", required_text("correctDoctype", correctDoctype)):
		raise InvalidArguments(f"DocType {correctDoctype} does not exist; list_doctypes shows the DocTypes")
	df = frappe.get_meta(required_text("doctype", doctype)).get_field(required_text("fieldname", fieldname))
	if not df or df.fieldtype not in ("Link", "Table", "Table MultiSelect"):
		raise InvalidArguments(f"{fieldname} is not a Link, Table or Table MultiSelect field of {doctype}")
	method = change_field(doctype, fieldname, {"options": correctDoctype})
	return {"fixed": fieldname, "doctype": doctype, "newOptions": correctDoctype, "method": method}


@tool("fix_stuck_document")
def fix_stuck_document(doctype, docName, targetState):
	workflow = frappe.db.get_value("Workflow", {"document_type": required_text("doctype", doctype), "is_active": 1}, "name")
	if not workflow:
		raise InvalidArguments(f"{doctype} has no active workflow")
	wf = frappe.get_doc("Workflow", workflow)
	state = next((s for s in wf.states if s.state == targetState), None)
	if not state:
		raise InvalidArguments(f"{targetState} is not a state of workflow {workflow}; its states are {', '.join(s.state for s in wf.states)}")
	doc = frappe.get_doc(doctype, required_text("docName", docName))
	doc.check_permission("write")
	previous = doc.get(wf.workflow_state_field)
	if previous and previous in [s.state for s in wf.states]:
		raise InvalidArguments(f"{doc.name} is in state {previous}, a state of workflow {workflow}, so it is not stuck; "
			"the workflow's normal actions apply")
	if int(state.doc_status or 0) != doc.docstatus:
		raise InvalidArguments(f"state {targetState} is for docstatus {state.doc_status}, but {doc.name} has docstatus {doc.docstatus}")
	# db_set, not save(): save() runs the workflow's transition check, which rejects any move out of a state that no
	# longer exists, the very thing being fixed. The comment keeps the bypass visible in the document's timeline.
	doc.db_set(wf.workflow_state_field, targetState)
	doc.add_comment("Edit", f"Workflow state changed from {previous} to {targetState} by fix_stuck_document")
	return {"fixed": doc.name, "doctype": doctype, "previousState": previous, "newState": targetState}


@tool("activate_workflow")
def activate_workflow(workflowName):
	doc = frappe.get_doc("Workflow", required_text("workflowName", workflowName))
	others = frappe.get_all("Workflow", filters={"document_type": doc.document_type, "is_active": 1, "name": ("!=", doc.name)}, pluck="name")
	doc.is_active = 1
	doc.save()  # Frappe deactivates the DocType's other workflows
	return {"activated": doc.name, "doctype": doc.document_type, "deactivated": others}


@tool("fix_permission")
def fix_permission(doctype, role, permissions=None):
	return grant(doctype, role, permissions)


@tool("fix_broken_link_value")
def fix_broken_link_value(doctype, recordName, fieldname, correctValue):
	df = frappe.get_meta(required_text("doctype", doctype)).get_field(required_text("fieldname", fieldname))
	if not df or df.fieldtype not in ("Link", "Dynamic Link"):
		raise InvalidArguments(f"{fieldname} is not a Link field of {doctype}")
	doc = frappe.get_doc(doctype, required_text("recordName", recordName))
	target = df.options if df.fieldtype == "Link" else doc.get(df.options)
	if correctValue and not frappe.db.exists(target, correctValue):
		raise InvalidArguments(f"{target} {correctValue} does not exist; an empty correctValue clears the link")
	doc.set(fieldname, correctValue or None)
	doc.save()
	return {"fixed": doc.name, "doctype": doctype, "field": fieldname, "newValue": correctValue}


@tool("fix_missing_required_value")
def fix_missing_required_value(doctype, recordName, fieldname, value):
	if not frappe.get_meta(required_text("doctype", doctype)).get_field(required_text("fieldname", fieldname)):
		raise InvalidArguments(f"{doctype} has no field {fieldname}")
	if value in (None, ""):
		raise InvalidArguments("value must not be empty")
	doc = frappe.get_doc(doctype, required_text("recordName", recordName))
	doc.set(fieldname, value)
	doc.save()
	return {"fixed": doc.name, "doctype": doctype, "field": fieldname, "value": value}
