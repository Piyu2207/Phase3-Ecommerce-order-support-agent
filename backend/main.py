from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .graph import (
    run_graph,
    resume_graph,
    forensics,
    get_refund_status,
)

app = FastAPI(
    title="E-Commerce Order Support Agent",
    version="1.0.0",
)


# ============================================================
# REQUEST MODELS
# ============================================================


class RunRequest(BaseModel):
    query: str
    thread_id: str
    user_id: str


class ApprovalRequest(BaseModel):
    thread_id: str


# ============================================================
# CUSTOMER REQUEST
# ============================================================


@app.post("/run")
def run(request: RunRequest):
    try:
        return run_graph(
            request.query,
            request.thread_id,
            request.user_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )


# ============================================================
# HITL APPROVAL
# ============================================================


@app.post("/approve")
def approve(request: ApprovalRequest):
    try:
        return resume_graph(
            request.thread_id,
            "approved",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )


# ============================================================
# HITL DENIAL
# ============================================================


@app.post("/deny")
def deny(request: ApprovalRequest):
    try:
        return resume_graph(
            request.thread_id,
            "denied",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )


# ============================================================
# CUSTOMER REFUND STATUS
#
# IMPORTANT:
# Status is looked up by ORDER ID, not by thread ID.
# This allows the customer to create a new chat and still
# see the correct refund status.
# ============================================================


@app.get("/refund-status/{order_id}")
def refund_status(
    order_id: str,
    user_id: str,
):
    try:
        return get_refund_status(
            order_id,
            user_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )


# ============================================================
# FORENSICS
# ============================================================


@app.get("/forensics/{thread_id}")
def get_forensics(
    thread_id: str,
    user_id: str,
):
    try:
        return forensics(
            thread_id,
            user_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )
