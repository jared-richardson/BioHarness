from __future__ import annotations

from scripts.run_agent_e2e_execution import AgentE2EExecutionMixin
from scripts.run_agent_e2e_plan_context import AgentE2EPlanContextMixin
from scripts.run_agent_e2e_plan_validation import AgentE2EPlanValidationMixin
from scripts.run_agent_e2e_planner_settings import AgentE2EPlannerSettingsMixin
from scripts.run_agent_e2e_planner_supervision import AgentE2EPlannerSupervisionMixin
from scripts.run_agent_e2e_preexecution_repairs import AgentE2EPreexecutionRepairMixin
from scripts.run_agent_e2e_runtime_repair_actions import AgentE2ERuntimeRepairActionMixin
from scripts.run_agent_e2e_runtime_repair_support import AgentE2ERuntimeRepairSupportMixin
from scripts.run_agent_e2e_runtime_repair_templates import AgentE2ERuntimeRepairTemplateMixin
from scripts.run_agent_e2e_stepwise_loop import AgentE2EStepwiseExecutionMixin
from scripts.run_agent_e2e_state import AgentE2EStateMixin


class AgentE2EHarness(
    AgentE2EStateMixin,
    AgentE2EPlanContextMixin,
    AgentE2EPlannerSettingsMixin,
    AgentE2EPlannerSupervisionMixin,
    AgentE2EPreexecutionRepairMixin,
    AgentE2EPlanValidationMixin,
    AgentE2EExecutionMixin,
    AgentE2EStepwiseExecutionMixin,
    AgentE2ERuntimeRepairSupportMixin,
    AgentE2ERuntimeRepairTemplateMixin,
    AgentE2ERuntimeRepairActionMixin,
):
    """Coordinate end-to-end harness planning, execution, and repair."""

    pass
