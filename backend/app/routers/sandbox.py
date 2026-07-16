from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.auth.dependencies import get_current_user
from app.models import User
from app.config import get_sandboxes
from app.sandbox.manager import check_sandbox_health

router = APIRouter(prefix="/sandboxes", tags=["sandbox"])


class SandboxStatus(BaseModel):
    id: str
    url: str
    healthy: bool


@router.get("")
async def list_sandboxes(user: User = Depends(get_current_user)):
    sandboxes = get_sandboxes()
    return [{"id": s["id"]} for s in sandboxes.values()]


@router.get("/{sandbox_id}/status", response_model=SandboxStatus)
async def sandbox_status(sandbox_id: str, user: User = Depends(get_current_user)):
    sandboxes = get_sandboxes()
    sandbox = sandboxes.get(sandbox_id)
    if not sandbox:
        return SandboxStatus(id=sandbox_id, url="", healthy=False)
    healthy = await check_sandbox_health(sandbox)
    return SandboxStatus(id=sandbox["id"], url=sandbox["url"], healthy=healthy)