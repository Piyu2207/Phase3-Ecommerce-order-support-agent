from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .config import DB_PATH, REFUND_THRESHOLD
from .guardrails import inspect_egress, inspect_ingress
from .state import AgentState
from .tools import (
    issue_refund,
    lookup_order,
    lookup_product,
)

# ============================================================
# LANGGRAPH CHECKPOINT STORE
# ============================================================

CHECKPOINT_CONTEXT = SqliteSaver.from_conn_string(DB_PATH)
CHECKPOINTER = CHECKPOINT_CONTEXT.__enter__()


# ============================================================
# PERSISTENT REFUND LEDGER
#
# Refund state is intentionally independent from thread_id.
# This prevents:
#
# Thread A -> approve ORD-1002
# Thread B -> ask ORD-1002
#
# from creating another refund.
# ============================================================

REFUND_DB = sqlite3.connect(
    DB_PATH,
    check_same_thread=False,
)

REFUND_DB.row_factory = sqlite3.Row

REFUND_DB.execute("""
    CREATE TABLE IF NOT EXISTS refund_ledger (
        order_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        amount REAL NOT NULL,
        reason TEXT NOT NULL,
        status TEXT NOT NULL,
        refund_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """)


# ============================================================
# REFUND LEDGER SCHEMA MIGRATION
# ============================================================

required_columns = {
    "user_id": """
        ALTER TABLE refund_ledger
        ADD COLUMN user_id TEXT NOT NULL DEFAULT ''
    """,
    "refund_id": """
        ALTER TABLE refund_ledger
        ADD COLUMN refund_id TEXT
    """,
}

refund_columns = {
    row["name"]
    for row in REFUND_DB.execute("PRAGMA table_info(refund_ledger)").fetchall()
}

if "user_id" not in refund_columns:
    REFUND_DB.execute("""
        ALTER TABLE refund_ledger
        ADD COLUMN user_id TEXT NOT NULL DEFAULT ''
        """)

if "amount" not in refund_columns:
    REFUND_DB.execute("""
        ALTER TABLE refund_ledger
        ADD COLUMN amount REAL NOT NULL DEFAULT 0
        """)

if "reason" not in refund_columns:
    REFUND_DB.execute("""
        ALTER TABLE refund_ledger
        ADD COLUMN reason TEXT NOT NULL DEFAULT ''
        """)

if "status" not in refund_columns:
    REFUND_DB.execute("""
        ALTER TABLE refund_ledger
        ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'
        """)


if "refund_id" not in refund_columns:
    REFUND_DB.execute("""
        ALTER TABLE refund_ledger
        ADD COLUMN refund_id TEXT
        """)


if "updated_at" not in refund_columns:
    REFUND_DB.execute("""
        ALTER TABLE refund_ledger
        ADD COLUMN updated_at TEXT
        """)

if "created_at" not in refund_columns:
    REFUND_DB.execute("""
        ALTER TABLE refund_ledger
        ADD COLUMN created_at TEXT NOT NULL DEFAULT ''
        """)


REFUND_DB.commit()


# ============================================================
# HELPERS
# ============================================================


def event(
    state: AgentState,
    name: str,
    **data: Any,
) -> list[dict[str, Any]]:
    events = list(state.get("events", []))

    events.append(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": name,
            **data,
        }
    )

    return events


def extract_order_id(q: str) -> str:
    match = re.search(
        r"\bORD[- ]?(\d{4,6})\b",
        q.upper(),
    )

    if not match:
        return ""

    return f"ORD-{match.group(1)}"


def extract_refund_amount(
    q: str,
) -> float | None:
    match = re.search(
        r"(?:₹|rs\.?|inr)\s*"
        r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)"
        r"|"
        r"\b([0-9][0-9,]*(?:\.[0-9]{1,2})?)"
        r"\s*(?:rupees|rs\.?)\b",
        q.lower(),
    )

    if not match:
        return None

    raw = next(group for group in match.groups() if group is not None)

    return float(raw.replace(",", ""))


def existing_refund(
    order_id: str,
) -> dict[str, Any] | None:
    row = REFUND_DB.execute(
        """
        SELECT *
        FROM refund_ledger
        WHERE order_id = ?
        """,
        (order_id.upper(),),
    ).fetchone()

    return dict(row) if row else None


def create_or_get_pending_refund(
    user_id: str,
    order_id: str,
    amount: float,
    reason: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()

    existing = existing_refund(order_id)

    if existing:
        return existing

    REFUND_DB.execute(
        """
        INSERT INTO refund_ledger
        (
            order_id,
            user_id,
            amount,
            reason,
            status,
            refund_id,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order_id.upper(),
            user_id,
            amount,
            reason,
            "pending",
            None,
            now,
            now,
        ),
    )

    REFUND_DB.commit()

    return existing_refund(order_id) or {}


def update_refund_status(
    order_id: str,
    status: str,
    refund_id: str | None = None,
) -> None:

    order_id = order_id.upper()

    print("\n" + "=" * 80)
    print("UPDATE REFUND STATUS")
    print("=" * 80)
    print("order_id:", order_id)
    print("status:", status)
    print("refund_id:", refund_id)

    # Check row BEFORE update
    before = REFUND_DB.execute(
        """
        SELECT *
        FROM refund_ledger
        WHERE order_id = ?
        """,
        (order_id,),
    ).fetchone()

    print("\nBEFORE UPDATE:")
    print(dict(before) if before else None)

    if not before:
        raise RuntimeError(f"Refund ledger row does not exist for {order_id}")

    REFUND_DB.execute(
        """
        UPDATE refund_ledger
        SET
            status = ?,
            refund_id = COALESCE(?, refund_id),
            updated_at = ?
        WHERE order_id = ?
        """,
        (
            status,
            refund_id,
            datetime.now(timezone.utc).isoformat(),
            order_id,
        ),
    )

    REFUND_DB.commit()

    # Check SQLite actually updated it
    after = REFUND_DB.execute(
        """
        SELECT *
        FROM refund_ledger
        WHERE order_id = ?
        """,
        (order_id,),
    ).fetchone()

    print("\nAFTER UPDATE:")
    print(dict(after) if after else None)

    print("rowcount:", REFUND_DB.total_changes)
    print("=" * 80)


def get_refund_status(
    order_id: str,
    user_id: str,
) -> dict[str, Any]:
    order_id = order_id.upper()

    row = existing_refund(order_id)

    if not row:
        return {
            "status": "not_found",
            "order_id": order_id,
            "message": ("No refund request has been submitted " "for this order."),
        }

    # The customer may only view the refund belonging to
    # the same customer who created it.
    if row["user_id"] != user_id:
        return {
            "status": "not_found",
            "order_id": order_id,
            "message": ("No refund request has been submitted " "for this order."),
        }

    return {
        "status": row["status"],
        "order_id": row["order_id"],
        "amount": row["amount"],
        "refund_id": row["refund_id"],
        "message": (
            "Refund is pending support review."
            if row["status"] == "pending"
            else (
                "Refund has been approved and processed."
                if row["status"] == "approved"
                else (
                    "Refund request has been denied."
                    if row["status"] == "denied"
                    else "Refund status available."
                )
            )
        ),
    }


# ============================================================
# GRAPH NODES
# ============================================================


def guardrail_node(
    state: AgentState,
) -> dict[str, Any]:
    ok, reason = inspect_ingress(state["user_query"])

    if not ok:
        return {
            "blocked": True,
            "guardrail_reason": reason,
            # Clear previous-turn request state.
            "category": "",
            "severity": "",
            "intent_confidence": 0.0,
            "order": {},
            "product": {},
            "specialist_notes": [],
            "pending_action": None,
            "approval_required": False,
            "approval_status": "",
            "refund_status": "",
            "refund_id": None,
            "response": (f"Blocked by guardrails: {reason}"),
            "events": event(
                state,
                "guardrail_block",
                reason=reason,
            ),
        }

    return {
        "blocked": False,
        "guardrail_reason": None,
        # A new allowed request starts without the previous
        # request's classification/refund/action data.
        "category": "",
        "severity": "",
        "intent_confidence": 0.0,
        "order": {},
        "product": {},
        "specialist_notes": [],
        "pending_action": None,
        "approval_required": False,
        "approval_status": "",
        "refund_status": "",
        "refund_id": None,
        "events": event(
            state,
            "guardrail_pass",
        ),
    }


def classify_node(
    state: AgentState,
) -> dict[str, Any]:
    q = state["user_query"].lower()

    if any(
        x in q
        for x in (
            "refund",
            "money back",
            "return my money",
            "reimburse",
        )
    ):
        category = "refund"

    elif any(
        x in q
        for x in (
            "fraud",
            "fraudulent",
            "unauthorized",
            "stolen",
            "charge i did not",
        )
    ):
        category = "fraud"

    elif any(
        x in q
        for x in (
            "where is",
            "tracking",
            "delivery",
            "shipped",
            "late",
            "shipping",
        )
    ):
        category = "shipping"

    elif any(
        x in q
        for x in (
            "product",
            "stock",
            "available",
            "price",
            "keyboard",
            "mouse",
            "hub",
        )
    ):
        category = "product"

    else:
        category = "general"

    severity = (
        "high" if category == "fraud" else "medium" if category == "refund" else "low"
    )

    return {
        "category": category,
        "severity": severity,
        "intent_confidence": 0.92,
        "events": event(
            state,
            "classified",
            category=category,
            severity=severity,
        ),
    }


def supervisor_node(
    state: AgentState,
) -> dict[str, Any]:
    category = state.get(
        "category",
        "general",
    )

    return {
        "events": event(
            state,
            "supervisor_dispatch",
            worker=f"{category}_specialist",
        )
    }


def shipping_specialist(
    state: AgentState,
) -> dict[str, Any]:
    order_id = extract_order_id(state["user_query"])

    if not order_id:
        return {
            "specialist_notes": ["Please provide an order ID such as ORD-1001."],
            "events": event(
                state,
                "shipping_lookup_missing_order_id",
            ),
        }

    order = lookup_order.invoke(order_id)

    if "error" in order:
        note = "I could not find that order in the mock CRM."
    else:
        note = (
            f"Order {order_id} is {order['status']}. "
            f"Carrier: {order.get('carrier') or 'not assigned yet'}; "
            f"tracking: {order.get('tracking') or 'not available'}; "
            f"amount: ₹{order['amount']}."
        )

    return {
        "order": order,
        "specialist_notes": [note],
        "events": event(
            state,
            "shipping_specialist_complete",
            order_id=order_id,
        ),
    }


def refund_specialist(
    state: AgentState,
) -> dict[str, Any]:
    user_id = state["user_id"]

    order_id = extract_order_id(state["user_query"])

    if not order_id:
        return {
            "pending_action": None,
            "specialist_notes": ["Please provide an order ID such as ORD-1003."],
            "events": event(
                state,
                "refund_missing_order_id",
            ),
        }

    order = lookup_order.invoke(order_id)

    if "error" in order:
        return {
            "order": order,
            "pending_action": None,
            "specialist_notes": [
                "I could not find that order, so I cannot prepare a refund."
            ],
            "events": event(
                state,
                "refund_lookup_failed",
                order_id=order_id,
            ),
        }

    # --------------------------------------------------------
    # IMPORTANT:
    # Check the persistent refund ledger BEFORE asking for
    # another approval.
    # --------------------------------------------------------

    existing = existing_refund(order_id)

    if existing:
        status = existing["status"]

        if status == "approved":
            note = (
                f"A refund for {order_id} for "
                f"₹{float(existing['amount']):.2f} "
                "has already been approved and processed.\n\n"
                "No additional refund request is required."
            )

            return {
                "order": order,
                "pending_action": None,
                "refund_status": "approved",
                "approval_status": "approved",
                "specialist_notes": [note],
                "events": event(
                    state,
                    "refund_already_approved",
                    order_id=order_id,
                ),
            }

        if status == "denied":
            note = (
                f"The refund request for {order_id} "
                "has already been denied.\n\n"
                "No additional refund request was created."
            )

            return {
                "order": order,
                "pending_action": None,
                "refund_status": "denied",
                "approval_status": "denied",
                "specialist_notes": [note],
                "events": event(
                    state,
                    "refund_already_denied",
                    order_id=order_id,
                ),
            }

        if status == "pending":
            action = {
                "type": "refund",
                "order_id": order_id,
                "amount": float(existing["amount"]),
                "reason": existing["reason"],
                "requires_approval": True,
            }

            return {
                "order": order,
                "pending_action": action,
                "refund_status": "pending",
                "approval_required": True,
                "specialist_notes": [
                    f"Refund request for {order_id} is already pending support review."
                ],
                "events": event(
                    state,
                    "refund_already_pending",
                    order_id=order_id,
                ),
            }

    order_amount = float(order["amount"])

    requested_amount = extract_refund_amount(state["user_query"])

    amount = requested_amount if requested_amount is not None else order_amount

    if amount <= 0 or amount > order_amount:
        note = (
            f"Refund request for {order_id} is "
            f"₹{amount:.2f}, but the maximum refundable "
            f"amount for this order is "
            f"₹{order_amount:.2f}. "
            "No refund write was prepared."
        )

        return {
            "order": order,
            "pending_action": None,
            "refund_status": "rejected_amount",
            "specialist_notes": [note],
            "events": event(
                state,
                "refund_rejected",
                requested_amount=amount,
                max_refundable=order_amount,
            ),
        }

    # All customer refunds above threshold go through HITL.
    # The customer never approves their own request.
    requires_approval = amount > REFUND_THRESHOLD

    pending = {
        "type": "refund",
        "order_id": order_id,
        "amount": amount,
        "reason": "Customer-requested refund",
        "requires_approval": requires_approval,
    }

    if requires_approval:
        create_or_get_pending_refund(
            user_id,
            order_id,
            amount,
            "Customer-requested refund",
        )

    note = f"Prepared refund request for {order_id} " f"for ₹{amount:.2f}."

    return {
        "order": order,
        "pending_action": pending,
        "refund_status": ("pending" if requires_approval else "not_required"),
        "approval_required": requires_approval,
        "specialist_notes": [note],
        "events": event(
            state,
            "refund_prepared",
            requested_amount=amount,
            order_amount=order_amount,
            amount=amount,
            requires_approval=requires_approval,
        ),
    }


def product_specialist(
    state: AgentState,
) -> dict[str, Any]:
    q = state["user_query"].lower()

    name = (
        "mechanical keyboard"
        if "keyboard" in q
        else "usb-c hub" if "hub" in q else "wireless mouse"
    )

    product = lookup_product.invoke(name)

    if "error" in product:
        return {
            "specialist_notes": ["I could not find that product."],
            "events": event(
                state,
                "product_lookup_failed",
            ),
        }

    note = (
        f"{product['name']} costs ₹{product['price']} "
        f"and has {product['stock']} units in mock stock. "
        f"Return window: {product['return_days']} days."
    )

    return {
        "product": product,
        "specialist_notes": [note],
        "events": event(
            state,
            "product_specialist_complete",
            sku=product.get("sku"),
        ),
    }


def fraud_specialist(
    state: AgentState,
) -> dict[str, Any]:
    note = (
        "Fraud/unauthorized-payment reports are treated as "
        "high severity and require human review before any "
        "financial write."
    )

    return {
        "specialist_notes": [note],
        "events": event(
            state,
            "fraud_specialist_complete",
        ),
    }


def general_specialist(
    state: AgentState,
) -> dict[str, Any]:
    note = (
        "I can help with shipping, refunds, product "
        "information, and fraud/order-security questions."
    )

    return {
        "specialist_notes": [note],
        "events": event(
            state,
            "general_specialist_complete",
        ),
    }


# ============================================================
# HITL GATE
#
# Customer does not get these controls.
# FastAPI /approve and /deny resume this graph.
# ============================================================


def approval_gate(
    state: AgentState,
) -> dict[str, Any]:
    action = state.get("pending_action")

    if not action or not action.get("requires_approval"):
        return {
            "approval_status": "not_required",
            "events": event(
                state,
                "approval_not_required",
            ),
        }

    order_id = action["order_id"]

    existing = existing_refund(order_id)

    # Already resolved in another thread.
    if existing:
        if existing["status"] == "approved":
            return {
                "pending_action": None,
                "approval_status": "approved",
                "refund_status": "approved",
                "events": event(
                    state,
                    "approval_skipped_already_approved",
                    order_id=order_id,
                ),
            }

        if existing["status"] == "denied":
            return {
                "pending_action": None,
                "approval_status": "denied",
                "refund_status": "denied",
                "events": event(
                    state,
                    "approval_skipped_already_denied",
                    order_id=order_id,
                ),
            }

    decision = interrupt(
        {
            "type": "refund_approval",
            "action": action,
            "message": (
                "Support/HITL must approve or deny this "
                "refund before the write tool can execute."
            ),
        }
    )

    return {
        "approval_status": (decision if isinstance(decision, str) else "denied"),
        "refund_status": (decision if isinstance(decision, str) else "denied"),
        "events": event(
            state,
            "approval_decision_received",
            decision=decision,
        ),
    }


# ============================================================
# REFUND WRITE
# ============================================================


def execute_write(
    state: AgentState,
) -> dict[str, Any]:
    action = state.get("pending_action")

    if not action or state.get("approval_status") not in (
        "approved",
        "edit_approved",
    ):
        return {
            "events": event(
                state,
                "write_skipped_without_approval",
            )
        }

    order_id = action["order_id"]

    # SECOND duplicate-protection check.
    existing = existing_refund(order_id)

    if existing and existing["status"] == "approved":
        return {
            "pending_action": None,
            "refund_status": "approved",
            "approval_status": "approved",
            "refund_id": existing["refund_id"],
            "specialist_notes": (
                state.get("specialist_notes", [])
                + [f"Refund for {order_id} was already processed."]
            ),
            "events": event(
                state,
                "refund_write_skipped_duplicate",
                order_id=order_id,
            ),
        }

    result = issue_refund.invoke(
        {
            "order_id": order_id,
            "amount": action["amount"],
            "reason": action["reason"],
        }
    )

    if not result.get("success"):
        return {
            "refund_status": "write_failed",
            "specialist_notes": (
                state.get("specialist_notes", []) + [f"Refund write failed: {result}"]
            ),
            "events": event(
                state,
                "refund_write_failed",
                result=result,
            ),
        }

    refund_id = result.get("refund_id")

    update_refund_status(
        order_id,
        "approved",
        refund_id,
    )

    return {
        "pending_action": None,
        "refund_status": "approved",
        "approval_status": "approved",
        "refund_id": refund_id,
        "specialist_notes": (
            state.get("specialist_notes", [])
            + ["The approved refund write has been executed."]
        ),
        "events": event(
            state,
            "refund_write_executed",
            result=result,
        ),
    }


# ============================================================
# RESPONSE
# ============================================================


def response_node(
    state: AgentState,
) -> dict[str, Any]:
    if state.get("blocked"):
        text = state.get(
            "response",
            "Request blocked.",
        )

    else:
        action = state.get("pending_action") or {}

        status = state.get("refund_status")

        if action.get("type") == "refund" and status == "pending":
            text = (
                f"### 💰 Refund request submitted for review\n\n"
                f"Your refund request for **{action['order_id']}** "
                f"for **₹{float(action['amount']):.2f}** "
                "has been prepared successfully.\n\n"
                "⏳ A support representative must approve this "
                "refund before it can be processed. "
                "You don't need to take any action."
            )

        elif status == "approved":
            action_or_order = action or {}

            order_id = (
                action_or_order.get("order_id")
                or state.get("order_id")
                or state.get(
                    "order",
                    {},
                ).get("order_id")
                or "this order"
            )

            amount = action_or_order.get("amount") or state.get("amount")

            if amount is not None:
                text = (
                    f"### ✅ Refund already processed\n\n"
                    f"A refund for **{order_id}** for "
                    f"**₹{float(amount):.2f}** has already "
                    "been approved and processed.\n\n"
                    "No additional refund request is required."
                )
            else:
                text = (
                    f"### ✅ Refund already processed\n\n"
                    f"A refund for **{order_id}** has already "
                    "been approved and processed.\n\n"
                    "No additional refund request is required."
                )

        elif status == "denied":
            text = (
                "### ❌ Refund already denied\n\n"
                "This refund request has already been denied.\n\n"
                "No additional refund request was created."
            )

        else:
            text = (
                "\n".join(
                    state.get(
                        "specialist_notes",
                        [],
                    )
                )
                or "No action was required."
            )

    ok, reason = inspect_egress(text)

    if not ok:
        text = "Response withheld by egress guardrail: " f"{reason}"

    return {
        "response": text,
        "events": event(
            state,
            "response_delivered",
            egress_passed=ok,
        ),
    }


# ============================================================
# ROUTING
# ============================================================


def route_after_guardrail(
    state: AgentState,
) -> str:
    if state.get("blocked", False):
        return "blocked_response"

    return "classify"


def route_after_classify(
    state: AgentState,
) -> str:
    return state.get(
        "category",
        "general",
    )


def route_after_approval(
    state: AgentState,
) -> str:
    return (
        "execute_write"
        if state.get("approval_status")
        in (
            "approved",
            "edit_approved",
        )
        else "response"
    )


# ============================================================
# BUILD GRAPH
# ============================================================


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node(
        "guardrail",
        guardrail_node,
    )
    graph.add_node(
        "classify",
        classify_node,
    )
    graph.add_node(
        "supervisor",
        supervisor_node,
    )
    graph.add_node(
        "shipping",
        shipping_specialist,
    )
    graph.add_node(
        "refund",
        refund_specialist,
    )
    graph.add_node(
        "product",
        product_specialist,
    )
    graph.add_node(
        "fraud",
        fraud_specialist,
    )
    graph.add_node(
        "general",
        general_specialist,
    )
    graph.add_node(
        "approval_gate",
        approval_gate,
    )
    graph.add_node(
        "execute_write",
        execute_write,
    )
    graph.add_node(
        "response",
        response_node,
    )

    graph.add_edge(
        START,
        "guardrail",
    )

    graph.add_conditional_edges(
        "guardrail",
        route_after_guardrail,
        {
            "classify": "classify",
            "blocked_response": "response",
        },
    )

    graph.add_edge(
        "classify",
        "supervisor",
    )

    graph.add_conditional_edges(
        "supervisor",
        route_after_classify,
        {
            "shipping": "shipping",
            "refund": "refund",
            "product": "product",
            "fraud": "fraud",
            "general": "general",
        },
    )

    for node in (
        "shipping",
        "product",
        "fraud",
        "general",
    ):
        graph.add_edge(
            node,
            "response",
        )

    graph.add_edge(
        "refund",
        "approval_gate",
    )

    graph.add_conditional_edges(
        "approval_gate",
        route_after_approval,
        {
            "execute_write": "execute_write",
            "response": "response",
        },
    )

    graph.add_edge(
        "execute_write",
        "response",
    )

    graph.add_edge(
        "response",
        END,
    )

    return graph.compile(checkpointer=CHECKPOINTER)


GRAPH = build_graph()


# ============================================================
# PUBLIC API
# ============================================================


def run_graph(
    query: str,
    thread_id: str,
    user_id: str,
) -> dict[str, Any]:
    config = {
        "configurable": {
            "thread_id": thread_id,
        }
    }

    result = GRAPH.invoke(
        {
            "user_query": query,
            "thread_id": thread_id,
            "user_id": user_id,
            # ------------------------------------------------
            # Clear previous-turn response/workflow state.
            # The current graph execution will populate these.
            # ------------------------------------------------
            "blocked": False,
            "guardrail_reason": None,
            "order": {},
            "product": {},
            "specialist_notes": [],
            "pending_action": None,
            "approval_required": False,
            "approval_status": "",
            "refund_status": "",
            "refund_id": None,
            "response": "",
            "events": [],
        },
        config=config,
    )

    interrupts = (
        result.get(
            "__interrupt__",
            [],
        )
        if isinstance(result, dict)
        else []
    )

    return {
        "state": result,
        "response": (result.get("response") if isinstance(result, dict) else None),
        "category": (result.get("category") if isinstance(result, dict) else None),
        "severity": (result.get("severity") if isinstance(result, dict) else None),
        "approval_required": bool(
            result.get(
                "approval_required",
                False,
            )
            if isinstance(result, dict)
            else False
        ),
        "interrupts": interrupts,
        "thread_id": thread_id,
    }


def resume_graph(
    thread_id: str,
    decision: str,
    edit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = {
        "configurable": {
            "thread_id": thread_id,
        }
    }

    # Find the persisted pending action before resuming.
    snapshot = GRAPH.get_state(config)

    values = snapshot.values if hasattr(snapshot, "values") else {}

    action = values.get("pending_action") or {}

    order_id = action.get("order_id")

    if order_id:
        existing = existing_refund(order_id)

        # Never approve/deny an already-resolved refund again.
        if existing and existing["status"] in {
            "approved",
            "denied",
        }:
            return {
                "state": values,
                "response": ("This refund has already been " f"{existing['status']}."),
                "approval_status": existing["status"],
                "refund_status": existing["status"],
                "interrupts": [],
                "thread_id": thread_id,
            }

    if edit:
        command = Command(
            update={"pending_action": edit},
            resume="edit_approved",
        )
    else:
        command = Command(resume=decision)

    result = GRAPH.invoke(
        command,
        config=config,
    )

    interrupts = (
        result.get(
            "__interrupt__",
            [],
        )
        if isinstance(result, dict)
        else []
    )

    return {
        "state": result,
        "response": (result.get("response") if isinstance(result, dict) else None),
        "approval_status": (
            result.get("approval_status") if isinstance(result, dict) else None
        ),
        "refund_status": (
            result.get("refund_status") if isinstance(result, dict) else None
        ),
        "interrupts": interrupts,
        "thread_id": thread_id,
    }


def forensics(
    thread_id: str,
    user_id: str,
) -> dict[str, Any]:
    config = {
        "configurable": {
            "thread_id": thread_id,
        }
    }

    history = list(GRAPH.get_state_history(config))

    timeline = []
    anomaly = None

    for idx, snapshot in enumerate(reversed(history)):
        values = snapshot.values if hasattr(snapshot, "values") else {}

        timeline.append(
            {
                "step": idx,
                "checkpoint_id": (
                    getattr(
                        snapshot,
                        "config",
                        {},
                    )
                    .get(
                        "configurable",
                        {},
                    )
                    .get("checkpoint_id")
                ),
                "category": values.get("category"),
                "approval_status": values.get("approval_status"),
                "refund_status": values.get("refund_status"),
                "events": values.get(
                    "events",
                    [],
                )[-1:],
            }
        )

    return {
        "thread_id": thread_id,
        "user_id": user_id,
        "checkpoints": len(history),
        "timeline": timeline,
        "anomaly": (
            anomaly
            or "No anomaly detected by the built-in approval/write consistency check."
        ),
    }
