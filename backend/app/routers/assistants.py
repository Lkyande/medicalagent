from pydantic import BaseModel
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models import User
from app.sandbox.manager import ensure_workspace, get_or_create_workspace
from app.services.assistant_installer import available_assistants, get_assistant, install_assistant


router = APIRouter(prefix="/assistants", tags=["assistants"])


class AssistantCatalogItem(BaseModel):
    id: str
    name: str
    description: str
    version: str
    baseAgent: str
    systemPrompt: str
    skills: list[str]
    tools: list[str]


class AssistantInstallResponse(AssistantCatalogItem):
    enabled: bool
    changed: bool
    mcpStatus: str
    requiresNewSession: bool


def catalog_item(assistant) -> AssistantCatalogItem:
    return AssistantCatalogItem(
        id=assistant.id,
        name=assistant.name,
        description=assistant.description,
        version=assistant.version,
        baseAgent=assistant.base_agent,
        systemPrompt=assistant.system_prompt,
        skills=list(assistant.skills),
        tools=list(assistant.tools),
    )


@router.get("", response_model=list[AssistantCatalogItem])
async def list_assistants(
    _user: User = Depends(get_current_user),
):
    return [catalog_item(assistant) for assistant in available_assistants()]


@router.post("/{assistant_id}/install", response_model=AssistantInstallResponse)
async def install_platform_assistant(
    assistant_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    assistant = get_assistant(assistant_id)
    sandbox, workspace = await get_or_create_workspace(user, db)
    await ensure_workspace(sandbox, workspace, db)
    changed = await install_assistant(sandbox, workspace, assistant)
    return AssistantInstallResponse(
        **catalog_item(assistant).model_dump(),
        enabled=True,
        changed=changed,
        mcpStatus="connected",
        requiresNewSession=changed,
    )
