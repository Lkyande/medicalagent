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
from app.routers import auth, dispatch, sandbox, business, health, workspace
from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Your Platform API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
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
