"""
Approval REST endpoints — FastAPI APIRouter.

Provides inspection and resolution endpoints for the approval system.
telegram-gateway calls POST /approval/{id}/respond when the owner
clicks Approve/Deny.

POST /approval/{id}/respond requires an X-Api-Key header matching
the AGENT_API_KEY environment variable.
"""

import os
from fastapi import APIRouter, Depends, HTTPException, Request, Security
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel

router = APIRouter(prefix="/approval", tags=["approval"])

_api_key_header = APIKeyHeader(name="X-Api-Key", auto_error=False)


def _require_api_key(api_key: str = Security(_api_key_header)):
    expected = os.getenv("AGENT_API_KEY", "")
    if not expected:
        raise HTTPException(status_code=500, detail="AGENT_API_KEY not configured on server")
    if not api_key or api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


class ApprovalResponse(BaseModel):
    status: str  # "approved" or "denied"
    resolved_by: str = "owner"


@router.get("/pending")
async def list_pending(request: Request):
    """List all pending approval requests."""
    manager = request.app.state.approval_manager
    return {"pending": manager.get_pending()}


@router.get("/{approval_id}")
async def get_approval(approval_id: str, request: Request):
    """Get a specific approval request by ID."""
    manager = request.app.state.approval_manager
    data = manager.get_request(approval_id)
    if not data:
        raise HTTPException(status_code=404, detail="Approval not found")
    return data


@router.post("/{approval_id}/respond", dependencies=[Depends(_require_api_key)])
async def respond_approval(
    approval_id: str, body: ApprovalResponse, request: Request
):
    """Resolve an approval request (called by telegram-gateway)."""
    manager = request.app.state.approval_manager
    if body.status not in ("approved", "denied"):
        raise HTTPException(status_code=400, detail="Status must be 'approved' or 'denied'")
    ok = manager.resolve(approval_id, body.status, body.resolved_by)
    if not ok:
        raise HTTPException(status_code=409, detail="Approval already resolved or not found")
    return {"approval_id": approval_id, "status": body.status}
