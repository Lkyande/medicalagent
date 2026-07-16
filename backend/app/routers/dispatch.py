from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from app.database import get_db
from app.models import User
from app.auth.dependencies import get_current_user
from app.sandbox.manager import ensure_workspace, get_or_create_workspace

router = APIRouter(prefix="/dispatch", tags=["dispatch"])


class DispatchResponse(BaseModel):
    sandboxId: str
    proxyBase: str
    workspace: str


@router.post("", response_model=DispatchResponse)
async def dispatch(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    sandbox, workspace = await get_or_create_workspace(user, db)
    await ensure_workspace(sandbox, workspace, db)
    return DispatchResponse(
        sandboxId=sandbox["id"],
        proxyBase="/opencode",
        workspace=workspace.directory,
    )
