from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import get_sandboxes
from app.models import User, UserWorkspace
import httpx
import asyncio
import base64
import time
import uuid
from urllib.parse import urlencode

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

_assignment_lock = asyncio.Lock()
_workspace_ready_lock = asyncio.Lock()
_workspace_verified_at: dict[str, float] = {}
_workspace_verify_ttl = 300.0
_workspace_ready_marker = "__OPENCODE_WORKSPACE_READY__"
_workspace_verify_script = (
    "const fs=require('fs');"
    "const ready=fs.statSync(process.argv[1]).isDirectory();"
    f"setTimeout(()=>{{process.stdout.write('{_workspace_ready_marker}'+ready);setTimeout(()=>{{}},300);}},500);"
)


async def get_workspace_root(sandbox: dict) -> str:
    configured = sandbox.get("workspace_root", "")
    if configured:
        return configured
    auth = None
    if sandbox.get("password"):
        auth = (sandbox.get("username", "opencode"), sandbox["password"])
    try:
        async with httpx.AsyncClient(timeout=10.0, auth=auth) as client:
            response = await client.get(f"{sandbox['url']}/path")
            response.raise_for_status()
            home = response.json().get("home")
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to detect the sandbox workspace root",
        ) from exc
    if not isinstance(home, str) or not home:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to detect the sandbox workspace root",
        )
    separator = "\\" if "\\" in home else "/"
    return home.rstrip("/\\") + separator + ".opencode" + separator + "workspaces"


def workspace_directory(root: str, workspace_id: str) -> str:
    separator = "\\" if "\\" in root else "/"
    return root.rstrip("/\\") + separator + workspace_id


async def get_or_assign_sandbox(user: User, db: AsyncSession) -> dict:
    if user.sandbox_id:
        sandboxes = get_sandboxes()
        if user.sandbox_id in sandboxes:
            return sandboxes[user.sandbox_id]

    async with _assignment_lock:
        locked = await db.execute(select(User).where(User.id == user.id).with_for_update())
        current_user = locked.scalar_one()
        if current_user.sandbox_id:
            sandboxes = get_sandboxes()
            if current_user.sandbox_id in sandboxes:
                user.sandbox_id = current_user.sandbox_id
                await db.commit()
                return sandboxes[current_user.sandbox_id]

        sandboxes = get_sandboxes()
        if not sandboxes:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No OpenCode sandboxes are configured",
            )

        result = await db.execute(
            select(User.sandbox_id, func.count(User.id))
            .where(User.sandbox_id.in_(list(sandboxes)))
            .group_by(User.sandbox_id)
        )
        counts = {sid: count for sid, count in result.all()}
        sid = min(sandboxes, key=lambda item: (counts.get(item, 0), item))
        current_user.sandbox_id = sid
        user.sandbox_id = sid
        await db.commit()
        return sandboxes[sid]


async def get_or_create_workspace(user: User, db: AsyncSession) -> tuple[dict, UserWorkspace]:
    sandbox = await get_or_assign_sandbox(user, db)
    root = await get_workspace_root(sandbox)
    async with _assignment_lock:
        result = await db.execute(
            select(UserWorkspace).where(UserWorkspace.user_id == user.id).with_for_update()
        )
        workspace = result.scalar_one_or_none()
        workspace_id = workspace.id if workspace else uuid.uuid4().hex
        directory = workspace_directory(root, workspace_id)
        if (
            workspace
            and workspace.sandbox_id == sandbox["id"]
            and workspace.directory == directory
        ):
            await db.commit()
            return sandbox, workspace

        if workspace:
            workspace.sandbox_id = sandbox["id"]
            workspace.directory = directory
            workspace.ready = False
        else:
            workspace = UserWorkspace(
                id=workspace_id,
                user_id=user.id,
                sandbox_id=sandbox["id"],
                directory=directory,
                ready=False,
            )
            db.add(workspace)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            result = await db.execute(select(UserWorkspace).where(UserWorkspace.user_id == user.id))
            workspace = result.scalar_one()
        return sandbox, workspace


def sandbox_auth(sandbox: dict) -> tuple[str, str] | None:
    if not sandbox.get("password"):
        return None
    return (sandbox.get("username", "opencode"), sandbox["password"])


async def run_sandbox_command_capture(
    sandbox: dict,
    directory: str,
    command: str,
    args: list[str],
    cwd: str,
    title: str,
    timeout_seconds: float = 15.0,
    capture_output: bool = True,
) -> tuple[int, str]:
    auth = sandbox_auth(sandbox)
    pty_id: str | None = None
    websocket = None
    receiver: asyncio.Task | None = None
    output: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=10.0, auth=auth) as client:
            response = await client.post(
                f"{sandbox['url']}/pty",
                params={"directory": directory},
                json={"command": command, "args": args, "cwd": cwd, "title": title},
            )
            response.raise_for_status()
            info = response.json()
            pty_id = info.get("id")
            if not isinstance(pty_id, str) or not pty_id:
                raise ValueError("Sandbox did not return a PTY id")

            if capture_output:
                scheme = "wss" if sandbox["url"].startswith("https://") else "ws"
                base_url = sandbox["url"].split("://", 1)[-1].rstrip("/")
                query = {"directory": directory, "cursor": "0"}
                if auth:
                    query["auth_token"] = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
                websocket = await asyncio.wait_for(
                    connect(f"{scheme}://{base_url}/pty/{pty_id}/connect?{urlencode(query)}"),
                    timeout=5.0,
                )

                async def receive_output() -> None:
                    try:
                        async for message in websocket:
                            if isinstance(message, bytes):
                                if message.startswith(b"\x00"):
                                    continue
                                output.append(message.decode("utf-8", errors="replace"))
                            else:
                                output.append(message)
                    except WebSocketException:
                        pass

                receiver = asyncio.create_task(receive_output())

            deadline = time.monotonic() + timeout_seconds
            while info.get("status") != "exited":
                if time.monotonic() >= deadline:
                    raise HTTPException(
                        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                        detail="Sandbox command timed out",
                    )
                await asyncio.sleep(0.1)
                response = await client.get(
                    f"{sandbox['url']}/pty/{pty_id}",
                    params={"directory": directory},
                )
                if response.status_code == status.HTTP_404_NOT_FOUND:
                    await asyncio.sleep(0.1)
                    return 0, "".join(output)
                response.raise_for_status()
                info = response.json()

            exit_code = info.get("exitCode")
            await asyncio.sleep(0.1)
            return (exit_code if isinstance(exit_code, int) else 1), "".join(output)
    except HTTPException:
        raise
    except (httpx.HTTPError, WebSocketException, OSError, ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to execute command in the sandbox",
        ) from exc
    finally:
        if receiver:
            receiver.cancel()
        if websocket:
            await websocket.close()
        if pty_id:
            try:
                async with httpx.AsyncClient(timeout=5.0, auth=auth) as client:
                    await client.delete(
                        f"{sandbox['url']}/pty/{pty_id}",
                        params={"directory": directory},
                    )
            except httpx.HTTPError:
                pass


async def run_sandbox_command(
    sandbox: dict,
    directory: str,
    command: str,
    args: list[str],
    cwd: str,
    title: str,
    timeout_seconds: float = 15.0,
) -> int:
    exit_code, _ = await run_sandbox_command_capture(
        sandbox=sandbox,
        directory=directory,
        command=command,
        args=args,
        cwd=cwd,
        title=title,
        timeout_seconds=timeout_seconds,
        capture_output=False,
    )
    return exit_code


async def ensure_workspace(sandbox: dict, workspace: UserWorkspace, db: AsyncSession) -> None:
    key = f"{sandbox['id']}\0{workspace.directory}"
    if time.monotonic() - _workspace_verified_at.get(key, 0) < _workspace_verify_ttl:
        return

    async with _workspace_ready_lock:
        if time.monotonic() - _workspace_verified_at.get(key, 0) < _workspace_verify_ttl:
            return

        windows = "\\" in workspace.directory or (
            len(workspace.directory) > 1 and workspace.directory[1] == ":"
        )
        if windows:
            command = "powershell.exe"
            args = [
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "& { param([string]$Target) [System.IO.Directory]::CreateDirectory($Target) | Out-Null }",
                workspace.directory,
            ]
            cwd = workspace.directory.split("\\", 1)[0] + "\\"
        else:
            command = "/bin/mkdir"
            args = ["-p", workspace.directory]
            cwd = "/"

        exit_code = await run_sandbox_command(
            sandbox=sandbox,
            directory=cwd,
            command=command,
            args=args,
            cwd=cwd,
            title="Initialize workspace",
        )
        if exit_code != 0:
            workspace.ready = False
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Unable to initialize the user workspace",
            )

        verify_code, verify_output = await run_sandbox_command_capture(
            sandbox=sandbox,
            directory=cwd,
            command="node",
            args=["-e", _workspace_verify_script, workspace.directory],
            cwd=cwd,
            title="Verify workspace",
        )
        if verify_code != 0 or f"{_workspace_ready_marker}true" not in verify_output.replace("\r\n", ""):
            workspace.ready = False
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Unable to initialize the user workspace",
            )

        workspace.ready = True
        await db.commit()
        _workspace_verified_at[key] = time.monotonic()


async def check_sandbox_health(sandbox: dict) -> bool:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            auth = None
            if sandbox.get("password"):
                auth = (sandbox.get("username", "opencode"), sandbox["password"])
            resp = await client.get(f"{sandbox['url']}/global/health", auth=auth)
            return resp.status_code == 200
    except Exception:
        return False
