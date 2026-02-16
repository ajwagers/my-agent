"""
Approval REST endpoints — FastAPI APIRouter.

Provides inspection and resolution endpoints for the approval system.
telegram-gateway calls POST /approval/{id}/respond when the owner
clicks Approve/Deny.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/approval", tags=["approval"])


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


@router.post("/{approval_id}/respond")
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
