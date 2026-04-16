"""Mock CRM API service for agent evaluation (FastAPI on port 9110).

This service is designed for error-recovery testing: the task YAML sets
ERROR_RATE=0.5 so roughly half of tool calls will fail with 429/500.
The agent must retry to complete the data export.
"""

from __future__ import annotations  # PEP 604 backport for Python 3.9

import copy
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Mock CRM API")

from mock_services._base import add_error_injection

add_error_injection(app)

FIXTURES_PATH = Path(os.environ.get(
    "CRM_FIXTURES",
    str(Path(__file__).resolve().parent.parent.parent / "tasks" / "T23zh_crm_data_export" / "fixtures" / "crm" / "customers.json"),
))

_customers: list[dict[str, Any]] = []
_audit_log: list[dict[str, Any]] = []
_exported_reports: list[dict[str, Any]] = []
_created_customers: list[dict[str, Any]] = []
_created_tasks: list[dict[str, Any]] = []
_next_customer_id: int = 900


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _infer_contact_person(customer: dict[str, Any]) -> str:
    notes = str(customer.get("notes", ""))
    for marker in ("联系人：", "联系人:", "项目经理：", "项目经理:"):
        if marker not in notes:
            continue
        tail = notes.split(marker, 1)[1].strip()
        tail = re.split(r"[，。,；;\s]", tail, maxsplit=1)[0].strip()
        if tail:
            return tail

    email = str(_first_non_empty(customer.get("contact_email"), customer.get("email"), ""))
    if email and "@" in email:
        return email.split("@", 1)[0]

    return str(_first_non_empty(customer.get("name"), customer.get("company"), customer.get("customer_id"), ""))


def _normalize_customer(customer: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(customer)
    deal_value = _first_non_empty(
        normalized.get("deal_value"),
        normalized.get("deal_amount"),
        normalized.get("annual_revenue"),
        0,
    )
    notes = str(normalized.get("notes", ""))

    normalized.setdefault("name", str(_first_non_empty(normalized.get("company"), normalized.get("customer_id"), "")))
    normalized.setdefault("company", str(_first_non_empty(normalized.get("company"), normalized.get("name"), "")))
    normalized.setdefault("contact_person", _infer_contact_person(normalized))
    normalized.setdefault("contact_email", str(_first_non_empty(normalized.get("email"), "")))
    normalized.setdefault("status", "active")
    normalized.setdefault(
        "tier",
        "VIP"
        if any(keyword in notes for keyword in ("VIP", "vip", "战略", "企业版")) or float(deal_value or 0) >= 300000
        else "standard",
    )
    normalized.setdefault("industry", "")
    normalized.setdefault("annual_revenue", deal_value or 0)
    normalized.setdefault("tasks", [])
    if normalized["tasks"] is None:
        normalized["tasks"] = []
    return normalized


def _load_fixtures() -> None:
    global _customers
    with open(FIXTURES_PATH) as f:
        raw_customers = json.load(f)

    _customers = [_normalize_customer(customer) for customer in raw_customers]


_load_fixtures()


def _log_call(endpoint: str, request_body: dict[str, Any], response_body: Any) -> None:
    _audit_log.append({
        "endpoint": endpoint,
        "request_body": request_body,
        "response_body": response_body,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


class ListCustomersRequest(BaseModel):
    status: Optional[str] = None
    tier: Optional[str] = None
    industry: Optional[str] = None


class GetCustomerRequest(BaseModel):
    customer_id: str


class ExportReportRequest(BaseModel):
    title: str
    customer_ids: list
    summary: str


class SearchCustomerRequest(BaseModel):
    email: Optional[str] = None
    company: Optional[str] = None


class CreateCustomerRequest(BaseModel):
    company_name: str
    contact_name: str
    contact_email: str
    contact_title: Optional[str] = None
    industry: Optional[str] = None
    notes: Optional[str] = None


class CreateTaskRequest(BaseModel):
    customer_id: str
    title: str
    description: Optional[str] = None
    priority: Optional[str] = "medium"
    due_date: Optional[str] = None


@app.post("/crm/customers")
def list_customers(req: ListCustomersRequest | None = None) -> dict[str, Any]:
    if req is None:
        req = ListCustomersRequest()
    results = []
    for c in _customers:
        if req.status and c["status"] != req.status:
            continue
        if req.tier and c["tier"] != req.tier:
            continue
        if req.industry and c["industry"] != req.industry:
            continue
        results.append({
            "customer_id": c["customer_id"],
            "name": c["name"],
            "company": c.get("company", c["name"]),
            "contact_person": c["contact_person"],
            "contact_email": c.get("contact_email", ""),
            "tier": c["tier"],
            "status": c["status"],
            "industry": c["industry"],
            "annual_revenue": c["annual_revenue"],
            "deal_stage": c.get("deal_stage", c.get("stage", "")),
            "deal_value": c.get("deal_value", c.get("deal_amount", 0)),
        })
    resp = {"customers": results, "total": len(results)}
    _log_call("/crm/customers", req.model_dump(), resp)
    return resp


@app.post("/crm/customers/get")
def get_customer(req: GetCustomerRequest) -> dict[str, Any]:
    for c in _customers:
        if c["customer_id"] == req.customer_id:
            resp = copy.deepcopy(c)
            _log_call("/crm/customers/get", req.model_dump(), resp)
            return resp
    resp = {"error": f"Customer {req.customer_id} not found"}
    _log_call("/crm/customers/get", req.model_dump(), resp)
    return resp


@app.post("/crm/export")
def export_report(req: ExportReportRequest) -> dict[str, Any]:
    report = {
        "title": req.title,
        "customer_ids": req.customer_ids,
        "summary": req.summary,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _exported_reports.append(report)
    resp = {"status": "exported", "report": report}
    _log_call("/crm/export", req.model_dump(), resp)
    return resp


@app.post("/crm/customers/search")
def search_customer(req: SearchCustomerRequest) -> dict[str, Any]:
    results = []
    for c in _customers:
        if req.email and req.email.lower() in str(c.get("contact_email", "")).lower():
            results.append(c)
        elif req.company and req.company.lower() in " ".join([str(c.get("name", "")), str(c.get("company", ""))]).lower():
            results.append(c)
    resp = {"customers": results, "total": len(results)}
    _log_call("/crm/customers/search", req.model_dump(), resp)
    return resp


@app.post("/crm/customers/create")
def create_customer(req: CreateCustomerRequest) -> dict[str, Any]:
    global _next_customer_id
    new_id = f"CUS-{_next_customer_id}"
    _next_customer_id += 1
    new_customer = {
        "customer_id": new_id,
        "name": req.company_name,
        "contact_person": req.contact_name,
        "contact_email": req.contact_email,
        "contact_title": req.contact_title or "",
        "status": "active",
        "tier": "standard",
        "industry": req.industry or "",
        "annual_revenue": 0,
        "notes": req.notes or "",
        "tasks": [],
    }
    _customers.append(new_customer)
    _created_customers.append(new_customer)
    resp = {"status": "created", "customer": new_customer}
    _log_call("/crm/customers/create", req.model_dump(), resp)
    return resp


@app.post("/crm/tasks/create")
def create_task(req: CreateTaskRequest) -> dict[str, Any]:
    task = {
        "task_id": f"TASK-{len(_created_tasks) + 1:03d}",
        "customer_id": req.customer_id,
        "title": req.title,
        "description": req.description or "",
        "priority": req.priority or "medium",
        "due_date": req.due_date or "",
        "status": "open",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _created_tasks.append(task)
    # Also add to customer's task list if found
    for c in _customers:
        if c["customer_id"] == req.customer_id:
            if "tasks" not in c:
                c["tasks"] = []
            c["tasks"].append(task)
            break
    resp = {"status": "created", "task": task}
    _log_call("/crm/tasks/create", req.model_dump(), resp)
    return resp


@app.get("/crm/audit")
def get_audit() -> dict[str, Any]:
    return {
        "calls": _audit_log,
        "exported_reports": _exported_reports,
        "created_customers": _created_customers,
        "tasks": _created_tasks,
    }


@app.post("/crm/reset")
def reset_state() -> dict[str, str]:
    global _audit_log, _exported_reports, _created_customers, _created_tasks, _next_customer_id
    _audit_log = []
    _exported_reports = []
    _created_customers = []
    _created_tasks = []
    _next_customer_id = 900
    _load_fixtures()
    return {"status": "reset"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "9110")))
