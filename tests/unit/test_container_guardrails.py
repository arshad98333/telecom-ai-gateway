"""The composition root is the only place the policy is chosen."""

from __future__ import annotations

from decimal import Decimal

from telecom_mcp.guardrails.pipeline import GuardrailPipeline
from tests.factory import build_test_application


def test_the_application_exposes_the_pipeline_it_built() -> None:
    harness = build_test_application()
    assert isinstance(harness.app.guardrails, GuardrailPipeline)


def test_the_executor_uses_that_same_pipeline_rather_than_its_own() -> None:
    harness = build_test_application()
    assert harness.executor.guardrails is harness.app.guardrails


def test_the_policy_comes_from_the_settings() -> None:
    harness = build_test_application(
        TELECOM_MCP_GUARDRAIL_WRITE_ACTIONS_PER_CASE="2",
        TELECOM_MCP_GUARDRAIL_REFUND_CEILING="1.50",
    )
    policy = harness.app.guardrails.policy
    assert policy.write_actions_per_case == 2
    assert policy.refund_ceiling == Decimal("1.50")


def test_switching_the_guardrails_off_is_visible_on_the_application() -> None:
    harness = build_test_application(TELECOM_MCP_GUARDRAILS_ENABLED="false")
    assert harness.app.guardrails.policy.enabled is False
