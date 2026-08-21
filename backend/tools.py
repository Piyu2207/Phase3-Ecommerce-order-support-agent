from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from .config import DB_PATH

# ============================================================
# MOCK DATA
# ============================================================

ORDERS = {
    "ORD-1001": {
        "order_id": "ORD-1001",
        "customer_id": "CUST-101",
        "status": "shipped",
        "carrier": "BlueDart",
        "tracking": "BD123456",
        "amount": 799,
        "items": ["Wireless Mouse"],
    },
    "ORD-1002": {
        "order_id": "ORD-1002",
        "customer_id": "CUST-102",
        "status": "delivered",
        "carrier": "Delhivery",
        "tracking": "DL987654",
        "amount": 1499,
        "items": ["Mechanical Keyboard"],
    },
    "ORD-1003": {
        "order_id": "ORD-1003",
        "customer_id": "CUST-103",
        "status": "processing",
        "carrier": None,
        "tracking": None,
        "amount": 1299,
        "items": ["USB-C Hub"],
    },
}


PRODUCTS = {
    "wireless mouse": {
        "sku": "WM-01",
        "name": "Wireless Mouse",
        "price": 799,
        "stock": 42,
        "return_days": 7,
    },
    "mechanical keyboard": {
        "sku": "KB-01",
        "name": "Mechanical Keyboard",
        "price": 1499,
        "stock": 18,
        "return_days": 7,
    },
    "usb-c hub": {
        "sku": "HUB-01",
        "name": "USB-C Hub",
        "price": 1299,
        "stock": 25,
        "return_days": 10,
    },
}


CUSTOMERS = {
    "CUST-101": {
        "customer_id": "CUST-101",
        "name": "Aarav",
        "tier": "Gold",
        "orders": 3,
    },
    "CUST-102": {
        "customer_id": "CUST-102",
        "name": "Meera",
        "tier": "Silver",
        "orders": 5,
    },
    "CUST-103": {
        "customer_id": "CUST-103",
        "name": "Riya",
        "tier": "Gold",
        "orders": 2,
    },
}


# ============================================================
# REFUND DATABASE
# ============================================================


def _connect_refund_db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row

    connection.execute("""
        CREATE TABLE IF NOT EXISTS refunds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            order_id TEXT NOT NULL,

            amount REAL NOT NULL,

            reason TEXT NOT NULL,

            status TEXT NOT NULL,

            thread_id TEXT,

            refund_id TEXT,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

    connection.commit()

    return connection


def initialize_refund_db():
    connection = _connect_refund_db()
    connection.close()


def get_latest_refund(order_id: str) -> dict[str, Any] | None:

    order_id = order_id.upper()

    connection = _connect_refund_db()

    row = connection.execute(
        """
        SELECT *
        FROM refunds
        WHERE order_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (order_id,),
    ).fetchone()

    connection.close()

    return dict(row) if row else None


def get_pending_refund(order_id: str) -> dict[str, Any] | None:

    order_id = order_id.upper()

    connection = _connect_refund_db()

    row = connection.execute(
        """
        SELECT *
        FROM refunds
        WHERE order_id = ?
        AND status = 'pending'
        ORDER BY id DESC
        LIMIT 1
        """,
        (order_id,),
    ).fetchone()

    connection.close()

    return dict(row) if row else None


def get_pending_refund_for_thread(
    thread_id: str,
) -> dict[str, Any] | None:

    connection = _connect_refund_db()

    row = connection.execute(
        """
        SELECT *
        FROM refunds
        WHERE thread_id = ?
        AND status = 'pending'
        ORDER BY id DESC
        LIMIT 1
        """,
        (thread_id,),
    ).fetchone()

    connection.close()

    return dict(row) if row else None


def create_pending_refund(
    order_id: str,
    amount: float,
    reason: str,
    thread_id: str,
) -> dict[str, Any]:

    order_id = order_id.upper()

    existing = get_latest_refund(order_id)

    if existing:

        if existing["status"] == "approved":
            return existing

        if existing["status"] == "pending":
            return existing

        if existing["status"] == "denied":
            # A denied request can be submitted again.
            pass

    connection = _connect_refund_db()

    cursor = connection.execute(
        """
        INSERT INTO refunds (
            order_id,
            amount,
            reason,
            status,
            thread_id
        )
        VALUES (?, ?, ?, 'pending', ?)
        """,
        (
            order_id,
            amount,
            reason,
            thread_id,
        ),
    )

    connection.commit()

    refund = connection.execute(
        """
        SELECT *
        FROM refunds
        WHERE id = ?
        """,
        (cursor.lastrowid,),
    ).fetchone()

    connection.close()

    return dict(refund)


def approve_refund(
    thread_id: str,
) -> dict[str, Any]:

    connection = _connect_refund_db()

    row = connection.execute(
        """
        SELECT *
        FROM refunds
        WHERE thread_id = ?
        AND status = 'pending'
        ORDER BY id DESC
        LIMIT 1
        """,
        (thread_id,),
    ).fetchone()

    if not row:

        connection.close()

        return {
            "success": False,
            "error": "No pending refund request exists for this thread.",
        }

    refund = dict(row)

    refund_id = refund["refund_id"] or f"REF-{refund['order_id']}-{refund['id']:03d}"

    connection.execute(
        """
        UPDATE refunds
        SET
            status = 'approved',
            refund_id = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            refund_id,
            refund["id"],
        ),
    )

    connection.commit()

    updated = connection.execute(
        """
        SELECT *
        FROM refunds
        WHERE id = ?
        """,
        (refund["id"],),
    ).fetchone()

    connection.close()

    return {
        "success": True,
        "refund": dict(updated),
    }


def deny_refund(
    thread_id: str,
) -> dict[str, Any]:

    connection = _connect_refund_db()

    row = connection.execute(
        """
        SELECT *
        FROM refunds
        WHERE thread_id = ?
        AND status = 'pending'
        ORDER BY id DESC
        LIMIT 1
        """,
        (thread_id,),
    ).fetchone()

    if not row:

        connection.close()

        return {
            "success": False,
            "error": "No pending refund request exists for this thread.",
        }

    refund = dict(row)

    connection.execute(
        """
        UPDATE refunds
        SET
            status = 'denied',
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (refund["id"],),
    )

    connection.commit()

    updated = connection.execute(
        """
        SELECT *
        FROM refunds
        WHERE id = ?
        """,
        (refund["id"],),
    ).fetchone()

    connection.close()

    return {
        "success": True,
        "refund": dict(updated),
    }


# ============================================================
# CUSTOMER TOOLS
# ============================================================


@tool
def lookup_order(order_id: str) -> dict[str, Any]:
    """Look up order status, tracking, amount and items in the mock CRM."""

    return ORDERS.get(
        order_id.upper(),
        {"error": "Order not found"},
    )


@tool
def lookup_customer(customer_id: str) -> dict[str, Any]:
    """Look up customer profile and loyalty tier in the mock CRM."""

    return CUSTOMERS.get(
        customer_id.upper(),
        {"error": "Customer not found"},
    )


@tool
def lookup_product(product_name: str) -> dict[str, Any]:
    """Look up product price, stock and return window."""

    key = product_name.strip().lower()

    for name, data in PRODUCTS.items():

        if key in name or name in key:
            return data

    return {"error": "Product not found"}


@tool
def issue_refund(
    order_id: str,
    amount: float,
    reason: str,
) -> dict[str, Any]:
    """
    Refund write operation.

    This function is kept as the financial write tool.
    The API approval workflow controls when it can execute.
    """

    order = ORDERS.get(order_id.upper())

    if not order:

        return {
            "success": False,
            "error": "Order not found",
        }

    if amount <= 0 or amount > float(order["amount"]):

        return {
            "success": False,
            "error": "Invalid refund amount",
        }

    existing = get_latest_refund(order_id)

    if existing and existing["status"] == "approved":

        return {
            "success": True,
            "already_processed": True,
            "refund_id": existing["refund_id"],
            "order_id": order_id.upper(),
            "amount": existing["amount"],
            "reason": existing["reason"],
            "status": "issued",
        }

    return {
        "success": True,
        "refund_id": f"REF-{order_id.upper()}-001",
        "order_id": order_id.upper(),
        "amount": amount,
        "reason": reason,
        "status": "issued",
    }


TOOLS = [
    lookup_order,
    lookup_customer,
    lookup_product,
    issue_refund,
]


initialize_refund_db()
