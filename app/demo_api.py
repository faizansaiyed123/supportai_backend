"""Standalone SupportAI demo API.

This module is intentionally isolated from app.main and the production database/worker stack.
It models the public HTTP contracts used by the frontend with synthetic, in-memory state.
Run with:
    uvicorn app.demo_api:app --host 127.0.0.1 --port 8001
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote, quote

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware

DEMO_PASSWORD = "demo1234"
ORG_ID = "00000000-0000-0000-0000-000000000001"

USERS: list[dict[str, Any]] = [
    {"id": "demo-owner", "email": "owner@supportai.demo", "password": DEMO_PASSWORD, "full_name": "Ava Patel", "role": "owner"},
    {"id": "demo-admin", "email": "admin@supportai.demo", "password": DEMO_PASSWORD, "full_name": "Noah Williams", "role": "admin"},
    {"id": "demo-agent", "email": "agent@supportai.demo", "password": DEMO_PASSWORD, "full_name": "Mia Chen", "role": "agent"},
    {"id": "demo-viewer", "email": "viewer@supportai.demo", "password": DEMO_PASSWORD, "full_name": "Ethan Brooks", "role": "viewer"},
]

PERMISSIONS = {
    "owner": {"knowledge:manage", "knowledge:read", "tickets:manage", "tickets:read", "customers:manage", "customers:read", "conversations:respond", "conversations:read", "members:manage", "members:read"},
    "admin": {"knowledge:manage", "knowledge:read", "tickets:manage", "tickets:read", "customers:manage", "customers:read", "conversations:respond", "conversations:read", "members:manage", "members:read"},
    "agent": {"knowledge:read", "tickets:manage", "tickets:read", "customers:manage", "customers:read", "conversations:respond", "conversations:read", "members:read"},
    "viewer": {"knowledge:read", "tickets:read", "customers:read", "conversations:read", "members:read"},
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _days(offset: int) -> str:
    return (datetime.now(UTC) + timedelta(days=offset)).isoformat()


def _copy(value: Any) -> Any:
    return deepcopy(value)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now(UTC).timestamp_ns()}"


def _seed() -> dict[str, list[dict[str, Any]]]:
    customers = [
        {"id": "customer-1", "external_id": "CUST-1001", "email": "jordan@acme.example", "name": "Jordan Lee", "created_at": _days(-18)},
        {"id": "customer-2", "external_id": "CUST-1002", "email": "sam@acme.example", "name": "Sam Rivera", "created_at": _days(-11)},
        {"id": "customer-3", "external_id": "CUST-1003", "email": "taylor@acme.example", "name": "Taylor Morgan", "created_at": _days(-6)},
    ]
    documents = [
        {"id": "doc-1", "title": "Refund policy", "original_filename": "refund-policy.md", "mime_type": "text/markdown", "status": "ready", "status_error": None, "created_at": _days(-14), "updated_at": _days(-14)},
        {"id": "doc-2", "title": "Shipping and delivery", "original_filename": "shipping.md", "mime_type": "text/markdown", "status": "ready", "status_error": None, "created_at": _days(-10), "updated_at": _days(-10)},
        {"id": "doc-3", "title": "Account security guide", "original_filename": "account-security.md", "mime_type": "text/markdown", "status": "ready", "status_error": None, "created_at": _days(-7), "updated_at": _days(-7)},
        {"id": "doc-4", "title": "Returns FAQ", "original_filename": "returns-faq.md", "mime_type": "text/markdown", "status": "ready", "status_error": None, "created_at": _days(-3), "updated_at": _days(-3)},
    ]
    conversations = [
        {"id": "conversation-1", "status": "open", "assigned_agent_id": "demo-agent", "created_at": _days(-2), "customer_id": "customer-1"},
        {"id": "conversation-2", "status": "escalated", "assigned_agent_id": "demo-agent", "created_at": _days(-5), "customer_id": "customer-2"},
        {"id": "conversation-3", "status": "closed", "assigned_agent_id": None, "created_at": _days(-9), "customer_id": "customer-3"},
    ]
    messages = [
        {"id": "message-1", "conversation_id": "conversation-1", "role": "customer", "content": "How long do I have to request a refund?", "created_at": _days(-2), "citations": []},
        {"id": "message-2", "conversation_id": "conversation-1", "role": "ai", "content": "Eligible purchases can be refunded within 30 days of delivery.", "created_at": _days(-2), "citations": [{"chunk_id": "chunk-refunds-1", "document_id": "doc-1", "heading_path": "Refund policy > Eligibility", "excerpt": "Customers can request a refund within 30 days of delivery for eligible purchases."}]},
        {"id": "message-3", "conversation_id": "conversation-2", "role": "customer", "content": "My package is late. What is the normal delivery window?", "created_at": _days(-5), "citations": []},
        {"id": "message-4", "conversation_id": "conversation-2", "role": "ai", "content": "Standard delivery usually takes 3–5 business days after dispatch.", "created_at": _days(-5), "citations": [{"chunk_id": "chunk-shipping-1", "document_id": "doc-2", "heading_path": "Shipping and delivery > Delivery windows", "excerpt": "Standard delivery usually arrives within 3–5 business days after dispatch."}]},
    ]
    tickets = [
        {"id": "ticket-1", "title": "Refund request for duplicate charge", "status": "open", "priority": "high", "customer_id": "customer-1", "assigned_agent_id": "demo-agent", "created_at": _days(-2)},
        {"id": "ticket-2", "title": "Shipment delayed beyond expected window", "status": "pending", "priority": "urgent", "customer_id": "customer-2", "assigned_agent_id": "demo-agent", "created_at": _days(-5)},
        {"id": "ticket-3", "title": "Password reset follow-up", "status": "resolved", "priority": "normal", "customer_id": "customer-3", "assigned_agent_id": None, "created_at": _days(-9)},
    ]
    notes = [
        {"id": "note-1", "ticket_id": "ticket-1", "author_id": "demo-agent", "content": "Verified the order and routed the refund request for review.", "created_at": _days(-2)},
        {"id": "note-2", "ticket_id": "ticket-2", "author_id": "demo-agent", "content": "Carrier scan shows the parcel moving through the regional hub.", "created_at": _days(-4)},
    ]
    memberships = [
        {"id": f"membership-{u['id']}", "user_id": u["id"], "email": u["email"], "full_name": u["full_name"], "role": u["role"], "status": "active", "created_at": _days(-30)}
        for u in USERS
    ]
    return {
        "customers": customers,
        "documents": documents,
        "conversations": conversations,
        "messages": messages,
        "tickets": tickets,
        "notes": notes,
        "memberships": memberships,
    }


STATE = _seed()


def reset_demo_state() -> None:
    global STATE
    STATE = _seed()


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in user.items() if key != "password"}


def _token(user: dict[str, Any]) -> str:
    return f"demo-access:{quote(user['id'])}"


def _current_user(request: Request) -> dict[str, Any]:
    value = request.headers.get("authorization", "")
    if not value.startswith("Bearer demo-access:"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_id = unquote(value.removeprefix("Bearer demo-access:"))
    user = next((item for item in USERS if item["id"] == user_id), None)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid demo session")
    return user


def _require(user: dict[str, Any], permission: str) -> None:
    if permission not in PERMISSIONS[user["role"]]:
        raise HTTPException(status_code=403, detail="Not permitted in this demo role")


def _get(name: str, item_id: str) -> dict[str, Any]:
    item = next((row for row in STATE[name] if row["id"] == item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return item


def _answer(question: str) -> dict[str, Any]:
    q = question.lower()
    if any(word in q for word in ("refund", "return", "30 days")):
        return {
            "message": "Eligible purchases can be refunded within 30 days of delivery. Start the request from the customer return flow and include the order you want reviewed.",
            "low_confidence": False,
            "citations": [{"chunk_id": "chunk-refunds-1", "document_id": "doc-1", "heading_path": "Refund policy > Eligibility", "excerpt": "Customers can request a refund within 30 days of delivery for eligible purchases."}],
        }
    if any(word in q for word in ("shipping", "delivery", "late", "arrive", "package")):
        return {
            "message": "Standard delivery usually arrives within 3–5 business days after dispatch. If the parcel is already beyond that window, escalating to a human agent is appropriate.",
            "low_confidence": False,
            "citations": [{"chunk_id": "chunk-shipping-1", "document_id": "doc-2", "heading_path": "Shipping and delivery > Delivery windows", "excerpt": "Standard delivery usually arrives within 3–5 business days after dispatch."}],
        }
    if any(word in q for word in ("password", "reset")):
        return {
            "message": "Use the password reset flow. Reset links are time-limited and the process does not disclose whether an email address exists.",
            "low_confidence": False,
            "citations": [{"chunk_id": "chunk-security-1", "document_id": "doc-3", "heading_path": "Account security > Password resets", "excerpt": "Password reset links are time-limited and never reveal whether an email exists."}],
        }
    return {
        "message": "I could not find enough grounded information in the demo knowledge base to answer that confidently. Consider escalating this conversation to a human agent.",
        "low_confidence": True,
        "citations": [],
    }


app = FastAPI(
    title="SupportAI Demo API",
    version="1.0.0",
    description="Synthetic in-memory API for recruiter demonstrations; no production services are used.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy", "environment": "demo", "storage": "in-memory", "ai": "deterministic-demo"}


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE"])
async def dispatch(path: str, request: Request) -> Any:
    segments = [unquote(item) for item in path.strip("/").split("/") if item]
    method = request.method
    body: dict[str, Any] = {}
    if method in {"POST", "PATCH", "PUT"}:
        try:
            value = await request.json()
            if isinstance(value, dict):
                body = value
        except Exception:
            body = {}

    if segments[:2] == ["auth", "login"] and method == "POST":
        user = next((u for u in USERS if u["email"].lower() == str(body.get("email", "")).lower() and u["password"] == body.get("password") and body.get("organization_slug") == "demo"), None)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        return {"access_token": _token(user), "token_type": "bearer", "expires_in_minutes": 30}

    if segments[:2] == ["auth", "refresh"] and method == "POST":
        user = _current_user(request)
        return {"access_token": _token(user), "token_type": "bearer", "expires_in_minutes": 30}

    if segments[:2] == ["auth", "logout"] and method == "POST":
        return Response(status_code=204)

    if segments[0:1] == ["auth"] and segments[1:] in (
        ["register"], ["password-reset", "request"], ["password-reset", "confirm"]
    ) and method == "POST":
        if segments[1:] == ["password-reset", "request"]:
            return {"detail": "If that email is registered, password reset instructions have been issued."}
        if segments[1:] == ["register"]:
            email = str(body.get("email", "")).lower()
            if any(user["email"].lower() == email for user in USERS):
                raise HTTPException(status_code=409, detail="Email already registered in the demo")
            user = {"id": _new_id("user"), "email": email, "password": body.get("password", DEMO_PASSWORD), "full_name": body.get("full_name", "Demo User"), "role": "owner"}
            USERS.append(user)
            STATE["memberships"].append({"id": _new_id("membership"), "user_id": user["id"], "email": user["email"], "full_name": user["full_name"], "role": "owner", "status": "active", "created_at": _now()})
            return _public_user(user)
        return Response(status_code=204)

    if segments[:3] == ["auth", "invites", "accept"] and method == "POST":
        user = {"id": _new_id("user"), "email": "invitee@supportai.demo", "password": body.get("password", DEMO_PASSWORD), "full_name": "Invited Teammate", "role": "agent"}
        USERS.append(user)
        STATE["memberships"].append({"id": _new_id("membership"), "user_id": user["id"], "email": user["email"], "full_name": user["full_name"], "role": "agent", "status": "active", "created_at": _now()})
        return {"access_token": _token(user), "token_type": "bearer", "expires_in_minutes": 30}

    user = _current_user(request)

    if segments[:3] == ["orgs", ORG_ID, "customers"]:
        _require(user, "customers:read")
        if method == "GET":
            return {"customers": _copy(STATE["customers"])}
        if method == "POST":
            _require(user, "customers:manage")
            customer = {
                "id": _new_id("customer"),
                "external_id": body.get("external_id"),
                "email": body.get("email"),
                "name": body.get("name"),
                "created_at": _now(),
            }
            STATE["customers"].insert(0, customer)
            return _copy(customer)

    if segments[:3] == ["orgs", ORG_ID, "memberships"]:
        _require(user, "members:read")
        if method == "GET":
            return {"memberships": _copy(STATE["memberships"])}
        if len(segments) == 4 and segments[3] == "invite" and method == "POST":
            _require(user, "members:manage")
            return {"user_id": _new_id("invitee"), "email": body.get("email"), "invite_token": f"demo-invite-{_new_id('token')}", "expires_in_hours": 72}
        if len(segments) == 5 and segments[4] == "role" and method == "POST":
            _require(user, "members:manage")
            member = _get("memberships", segments[3])
            member["role"] = body.get("role", member["role"])
            return _copy(member)

    if segments[:3] == ["orgs", ORG_ID, "conversations"]:
        _require(user, "conversations:read")
        if len(segments) == 3 and method == "GET":
            return {"conversations": _copy(STATE["conversations"])}
        if len(segments) == 3 and method == "POST":
            _require(user, "conversations:respond")
            conversation = {"id": _new_id("conversation"), "status": "open", "assigned_agent_id": "demo-agent", "created_at": _now(), "customer_id": None}
            STATE["conversations"].insert(0, conversation)
            return _copy(conversation)
        if len(segments) == 5 and segments[4] == "messages" and method == "GET":
            _get("conversations", segments[3])
            return {"messages": _copy([m for m in STATE["messages"] if m["conversation_id"] == segments[3]])}
        if len(segments) == 5 and segments[4] == "messages" and method == "POST":
            _require(user, "conversations:respond")
            conversation = _get("conversations", segments[3])
            result = _answer(str(body.get("question", "")))
            STATE["messages"].extend([
                {"id": _new_id("message"), "conversation_id": conversation["id"], "role": "customer", "content": str(body.get("question", "")), "created_at": _now(), "citations": []},
                {"id": _new_id("message"), "conversation_id": conversation["id"], "role": "ai", "content": result["message"], "created_at": _now(), "citations": result["citations"]},
            ])
            if result["low_confidence"]:
                conversation["status"] = "escalated"
            return result
        if len(segments) == 5 and segments[4] == "escalate" and method == "POST":
            _require(user, "conversations:respond")
            conversation = _get("conversations", segments[3])
            conversation["status"] = "escalated"
            return _copy(conversation)

    if segments[:3] == ["orgs", ORG_ID, "documents"]:
        _require(user, "knowledge:read")
        if len(segments) == 3 and method == "GET":
            return {"documents": _copy(STATE["documents"])}
        if len(segments) == 3 and method == "POST":
            _require(user, "knowledge:manage")
            document = {"id": _new_id("doc"), "title": "Demo uploaded document", "original_filename": body.get("filename"), "mime_type": body.get("mime_type", "text/plain"), "status": "ready", "status_error": None, "created_at": _now(), "updated_at": _now()}
            STATE["documents"].insert(0, document)
            return _copy(document)
        if len(segments) == 4 and method == "GET":
            return _copy(_get("documents", segments[3]))
        if len(segments) == 4 and method == "DELETE":
            _require(user, "knowledge:manage")
            document = _get("documents", segments[3])
            STATE["documents"].remove(document)
            return Response(status_code=204)
        if len(segments) == 5 and segments[4] == "versions" and method == "POST":
            _require(user, "knowledge:manage")
            document = _get("documents", segments[3])
            document["status"] = "ready"
            document["updated_at"] = _now()
            return _copy(document)

    if segments[:3] == ["orgs", ORG_ID, "tickets"]:
        _require(user, "tickets:read")
        if len(segments) == 3 and method == "GET":
            requested = request.query_params.get("status")
            rows = STATE["tickets"] if not requested else [t for t in STATE["tickets"] if t["status"] == requested]
            return {"tickets": _copy(rows)}
        if len(segments) == 3 and method == "POST":
            _require(user, "tickets:manage")
            ticket = {"id": _new_id("ticket"), "title": body.get("title"), "status": "open", "priority": body.get("priority", "normal"), "customer_id": body.get("customer_id"), "assigned_agent_id": None, "created_at": _now()}
            STATE["tickets"].insert(0, ticket)
            return _copy(ticket)
        if len(segments) == 4 and method == "GET":
            return _copy(_get("tickets", segments[3]))
        if len(segments) == 5 and segments[4] == "status" and method == "POST":
            _require(user, "tickets:manage")
            ticket = _get("tickets", segments[3])
            if body.get("status") not in {"open", "pending", "resolved", "closed"}:
                raise HTTPException(status_code=422, detail="Invalid ticket status")
            ticket["status"] = body["status"]
            return _copy(ticket)
        if len(segments) == 5 and segments[4] == "assign" and method == "POST":
            _require(user, "tickets:manage")
            ticket = _get("tickets", segments[3])
            ticket["assigned_agent_id"] = body.get("agent_id")
            ticket["status"] = "pending"
            return _copy(ticket)
        if len(segments) == 5 and segments[4] == "notes" and method == "GET":
            _get("tickets", segments[3])
            return {"notes": _copy([n for n in STATE["notes"] if n["ticket_id"] == segments[3]])}
        if len(segments) == 5 and segments[4] == "notes" and method == "POST":
            _require(user, "tickets:manage")
            _get("tickets", segments[3])
            note = {"id": _new_id("note"), "ticket_id": segments[3], "author_id": user["id"], "content": body.get("content", ""), "created_at": _now()}
            STATE["notes"].append(note)
            return _copy(note)

    raise HTTPException(status_code=404, detail="Demo endpoint not found")
