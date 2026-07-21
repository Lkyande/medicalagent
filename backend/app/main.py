from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from app.config import settings
from app.database import init_db, get_db
from app.auth.dependencies import get_current_user
from app.models import User
from app.proxy.opencode_proxy import proxy_to_sandbox
from app.sandbox.manager import ensure_workspace, get_or_create_workspace
from app.routers import assistants, auth, dispatch, education, interactions, sandbox, business, health, workspace, wechat
from app.mcp_server import murtagh_mcp
from app.education_mcp_server import education_mcp
from app.hot_topics_mcp_server import hot_topics_mcp
from app.middleware.request_size import UploadSizeLimitMiddleware
from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with (
        murtagh_mcp.session_manager.run(),
        education_mcp.session_manager.run(),
        hot_topics_mcp.session_manager.run(),
    ):
        await init_db()
        yield


app = FastAPI(title="Your Platform API", version="0.1.0", lifespan=lifespan)

app.add_middleware(UploadSizeLimitMiddleware, maximum_bytes=55 * 1024 * 1024)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(dispatch.router, prefix="/api")
app.include_router(sandbox.router, prefix="/api")
app.include_router(business.router, prefix="/api")
app.include_router(workspace.router, prefix="/api")
app.include_router(assistants.router, prefix="/api")
app.include_router(education.router, prefix="/api")
app.include_router(interactions.router, prefix="/api")
app.include_router(wechat.router, prefix="/api")
app.mount("/mcp/murtagh", murtagh_mcp.streamable_http_app())
app.mount("/mcp/education", education_mcp.streamable_http_app())
app.mount("/mcp/hot-topics", hot_topics_mcp.streamable_http_app())


@app.api_route(
    "/opencode/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
async def opencode_proxy(
    path: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sandbox, workspace = await get_or_create_workspace(user, db)
    await ensure_workspace(sandbox, workspace, db)
    return await proxy_to_sandbox(path, request, sandbox, user, workspace, db)
