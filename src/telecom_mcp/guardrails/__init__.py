"""The guardrail layer: what the authorization kernel is not for.

Authorization answers "may this identity do this". Guardrails answer a different
question - "is this call itself sane" - and they answer it on both sides of the
backend, because a well-formed request can still carry an injection payload and a
correctly authorized response can still carry a secret.

They sit outside the kernel on purpose. The kernel is a security boundary and its
stage order is frozen; guardrails are tuned against real traffic and change often.
Mixing the two would mean editing a security control every time a threshold moves.
"""

from telecom_mcp.guardrails.decision import (
    ALLOWED,
    DEFAULT_PUBLIC_MESSAGE,
    GuardrailDecision,
    GuardrailStage,
    GuardrailViolation,
)

__all__ = [
    "ALLOWED",
    "DEFAULT_PUBLIC_MESSAGE",
    "GuardrailDecision",
    "GuardrailStage",
    "GuardrailViolation",
]
