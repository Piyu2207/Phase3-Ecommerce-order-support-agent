import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_all_python_files_parse():
    for path in ROOT.rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"))


def test_required_files_exist():
    required = [
        "backend/state.py",
        "backend/graph.py",
        "backend/tools.py",
        "backend/guardrails.py",
        "backend/main.py",
        "frontend/app.py",
        "README.md",
        "requirements.txt",
    ]
    assert all((ROOT / p).exists() for p in required)


def test_phase3_keywords_present():
    graph = (ROOT / "backend/graph.py").read_text(encoding="utf-8")
    main = (ROOT / "backend/main.py").read_text(encoding="utf-8")
    assert "StateGraph" in graph
    assert "interrupt" in graph
    assert "SqliteSaver" in graph
    assert "get_state_history" in graph
    assert "/approve" in main and "/deny" in main and "/forensics" in main


def test_write_is_approval_gated():
    graph = (ROOT / "backend/graph.py").read_text(encoding="utf-8")
    assert "state.get('approval_status') not in ('approved', 'edit_approved')" in graph


def test_refund_amount_from_user_query_and_threshold():
    graph = (ROOT / "backend/graph.py").read_text(encoding="utf-8")
    assert "extract_refund_amount" in graph
    assert "requested_amount" in graph
    assert "requires_approval = amount > REFUND_THRESHOLD" in graph


def test_refund_response_uses_pending_action_amount():
    graph = (ROOT / "backend/graph.py").read_text(encoding="utf-8")
    assert "action.get('type') == 'refund'" in graph
    assert "action['order_id']" in graph
    assert "action['amount']" in graph
