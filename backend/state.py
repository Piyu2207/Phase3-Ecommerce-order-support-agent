from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    user_id: str
    thread_id: str
    user_query: str

    blocked: bool
    guardrail_reason: str | None

    category: str
    severity: str
    intent_confidence: float

    order: dict[str, Any]
    product: dict[str, Any]

    specialist_notes: list[str]

    pending_action: dict[str, Any] | None

    approval_required: bool
    approval_status: str

    # Persistent refund state.
    refund_status: str
    refund_id: str | None

    response: str

    events: list[dict[str, Any]]
