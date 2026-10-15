from erpsmith import guards
from erpsmith.registry import tool

tool("submit_plan")(guards.submit_plan)
tool("approve_plan")(guards.approve_plan)
tool("get_current_plan")(guards.get_current_plan)
