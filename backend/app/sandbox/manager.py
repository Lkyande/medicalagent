from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import get_sandboxes
from app.models import User, UserWorkspace
import httpx
import asyncio
import base64
import json
import re
import time
import uuid
from contextlib import suppress
from pathlib import PurePosixPath
from typing import BinaryIO
from urllib.parse import urlencode

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

_assignment_lock = asyncio.Lock()
_workspace_ready_lock = asyncio.Lock()
_workspace_verified_at: dict[str, float] = {}
_workspace_verify_ttl = 300.0
_workspace_ready_marker = "__OPENCODE_WORKSPACE_READY__"
_workspace_roots: dict[str, str] = {}
_workspace_root_locks: dict[str, asyncio.Lock] = {}
_workspace_verify_script = (
    "const fs=require('fs');"
    "const ready=fs.statSync(process.argv[1]).isDirectory();"
    f"process.stdout.write('{_workspace_ready_marker}'+ready);"
)
_command_runner_script = r"""
const { spawn } = require("child_process");
const payload = JSON.parse(Buffer.from(process.argv[1], "base64").toString("utf8"));
let started = false;
let finished = false;

function finish(code) {
  if (finished) return;
  finished = true;
  const exitCode = Number.isInteger(code) ? code : 1;
  process.stdout.write(`\n${payload.marker}${exitCode}\n`, () => {
    setTimeout(() => process.exit(exitCode), 100);
  });
}

process.stdin.setEncoding("utf8");
process.stdin.resume();
process.stdin.once("data", () => {
  if (started) return;
  started = true;
  let child;
  try {
    child = spawn(payload.command, payload.args, {
      cwd: payload.cwd,
      stdio: ["ignore", "inherit", "inherit"],
      windowsHide: true
    });
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    finish(1);
    return;
  }
  child.once("error", (error) => {
    console.error(error instanceof Error ? error.message : String(error));
    finish(1);
  });
  child.once("exit", (code) => finish(code));
});
""".strip()
_upload_receiver_script = r"""
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { pathToFileURL } = require("url");

const rootInput = process.argv[1];
const relative = process.argv[2];
const expectedSize = Number(process.argv[3]);
const expectedHash = process.argv[4];
const marker = process.argv[5];
const transferID = process.argv[6];
let handle;
let temporary;
let target;
let parentReal;
let rootReal;
let received = 0;
let sequence = 0;
let input = "";
let chain = Promise.resolve();
let finished = false;
const hash = crypto.createHash("sha256");

function emit(message, onFlushed) {
  process.stdout.write(marker + JSON.stringify(message) + "\n", onFlushed);
}

function contained(root, candidate) {
  const relativePath = path.relative(root, candidate);
  return relativePath === "" || (
    relativePath !== ".." &&
    !relativePath.startsWith(".." + path.sep) &&
    !path.isAbsolute(relativePath)
  );
}

async function cleanup() {
  if (handle) {
    try { await handle.close(); } catch (_) {}
    handle = undefined;
  }
  if (temporary) {
    try { await fs.promises.unlink(temporary); } catch (_) {}
  }
}

async function fail(error) {
  if (finished) return;
  finished = true;
  await cleanup();
  emit(
    { type: "error", detail: error instanceof Error ? error.message : String(error) },
    () => setTimeout(() => process.exit(1), 100),
  );
}

async function writeAll(buffer) {
  let offset = 0;
  while (offset < buffer.length) {
    const result = await handle.write(buffer, offset, buffer.length - offset, null);
    if (!result.bytesWritten) throw new Error("Unable to write upload data");
    offset += result.bytesWritten;
  }
}

async function frame(line) {
  if (finished || !line) return;
  const message = JSON.parse(line);
  if (message.type === "chunk") {
    if (message.seq !== sequence || typeof message.data !== "string") {
      throw new Error("Invalid upload sequence");
    }
    const chunk = Buffer.from(message.data, "base64");
    if (received + chunk.length > expectedSize) throw new Error("Upload exceeds expected size");
    await writeAll(chunk);
    hash.update(chunk);
    received += chunk.length;
    emit({ type: "ack", seq: sequence });
    sequence += 1;
    return;
  }
  if (message.type !== "finish" || message.seq !== sequence) {
    throw new Error("Invalid upload frame");
  }
  if (received !== expectedSize) throw new Error("Upload size mismatch");
  const actualHash = hash.digest("hex");
  if (actualHash !== expectedHash) throw new Error("Upload hash mismatch");
  await handle.sync();
  await handle.close();
  handle = undefined;

  const rootAgain = await fs.promises.realpath(rootInput);
  const parentAgain = await fs.promises.realpath(path.dirname(target));
  if (rootAgain !== rootReal) throw new Error("Workspace root changed during upload");
  if (parentAgain !== parentReal || !contained(rootAgain, parentAgain)) {
    throw new Error("Upload parent changed during upload");
  }
  const finalTarget = path.join(parentAgain, path.basename(target));
  try {
    await fs.promises.lstat(finalTarget);
    throw new Error("Upload target already exists");
  } catch (error) {
    if (!error || error.code !== "ENOENT") throw error;
  }
  await fs.promises.rename(temporary, finalTarget);
  temporary = undefined;
  finished = true;
  const url = pathToFileURL(finalTarget).href;
  for (let offset = 0; offset < url.length; offset += 32) {
    emit({ type: "url", data: url.slice(offset, offset + 32) });
  }
  emit({ type: "done" }, () => setTimeout(() => process.exit(0), 100));
}

async function initialize() {
  const root = await fs.promises.realpath(rootInput);
  rootReal = root;
  target = path.resolve(root, ...relative.split("/"));
  const targetParent = path.dirname(target);
  if (!contained(root, targetParent)) throw new Error("Upload target is outside the workspace");
  await fs.promises.mkdir(targetParent, { recursive: true });
  parentReal = await fs.promises.realpath(targetParent);
  if (!contained(root, parentReal)) throw new Error("Upload parent is outside the workspace");
  temporary = path.join(parentReal, `.upload-${transferID}.tmp`);
  handle = await fs.promises.open(temporary, "wx", 0o600);
  if (process.stdin.isTTY && process.stdin.setRawMode) process.stdin.setRawMode(true);
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", (chunk) => {
    input += chunk;
    let newline;
    while ((newline = input.indexOf("\n")) >= 0) {
      const line = input.slice(0, newline).replace(/\r$/, "");
      input = input.slice(newline + 1);
      chain = chain.then(() => frame(line)).catch(fail);
    }
  });
  process.stdin.on("end", () => chain.then(() => {
    if (!finished) return fail(new Error("Upload stream ended early"));
  }));
  emit({ type: "ready" });
}

initialize().catch(fail);
""".strip()

_replace_uploaded_file_script = r"""
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const rootInput = process.argv[1];
const temporaryRelative = process.argv[2];
const targetRelative = process.argv[3];
const expectedHash = process.argv[4];
const transferID = process.argv[5];

function contained(root, candidate) {
  const relativePath = path.relative(root, candidate);
  return relativePath === "" || (
    relativePath !== ".." &&
    !relativePath.startsWith(".." + path.sep) &&
    !path.isAbsolute(relativePath)
  );
}

async function hashFile(filename) {
  const hash = crypto.createHash("sha256");
  const stream = fs.createReadStream(filename);
  for await (const chunk of stream) hash.update(chunk);
  return hash.digest("hex");
}

async function main() {
  const root = await fs.promises.realpath(rootInput);
  const temporary = path.resolve(root, ...temporaryRelative.split("/"));
  const target = path.resolve(root, ...targetRelative.split("/"));
  const targetParent = await fs.promises.realpath(path.dirname(target));
  const temporaryReal = await fs.promises.realpath(temporary);
  if (!contained(root, targetParent) || !contained(root, temporaryReal)) {
    throw new Error("Replacement path is outside the workspace");
  }
  const targetStat = await fs.promises.lstat(target);
  if (!targetStat.isFile() || targetStat.isSymbolicLink()) {
    throw new Error("Replacement target is not a regular file");
  }
  if (await hashFile(target) !== expectedHash) process.exit(3);

  const backup = path.join(targetParent, `.replace-${transferID}.bak`);
  await fs.promises.rename(target, backup);
  try {
    if (await hashFile(backup) !== expectedHash) {
      await fs.promises.rename(backup, target);
      process.exit(3);
    }
    await fs.promises.rename(temporaryReal, target);
    try { await fs.promises.unlink(backup); } catch (_) {}
  } catch (error) {
    try {
      await fs.promises.lstat(target);
    } catch (_) {
      try { await fs.promises.rename(backup, target); } catch (_) {}
    }
    throw error;
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
""".strip()

_replace_file_locks: dict[str, asyncio.Lock] = {}
_delete_uploaded_files_script = r"""
const fs = require("fs");
const path = require("path");
const rootInput = process.argv[1];
const files = JSON.parse(Buffer.from(process.argv[2], "base64").toString("utf8"));

function contained(root, candidate) {
  const relativePath = path.relative(root, candidate);
  return relativePath === "" || (
    relativePath !== ".." &&
    !relativePath.startsWith(".." + path.sep) &&
    !path.isAbsolute(relativePath)
  );
}

(async () => {
  const root = await fs.promises.realpath(rootInput);
  for (const relative of files) {
    try {
      const target = path.resolve(root, ...relative.split("/"));
      if (!contained(root, target)) continue;
      const parent = await fs.promises.realpath(path.dirname(target));
      if (!contained(root, parent)) continue;
      const stat = await fs.promises.lstat(target);
      if (stat.isFile() || stat.isSymbolicLink()) await fs.promises.unlink(target);
    } catch (_) {}
  }
})().catch(() => process.exit(1));
""".strip()

_upload_chunk_bytes = 48 * 1024
_ansi_csi_pattern = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _parse_upload_event(payload: str) -> dict | None:
    try:
        event = json.loads(_ansi_csi_pattern.sub("", payload.rstrip("\r")))
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


async def get_workspace_root(sandbox: dict) -> str:
    configured = sandbox.get("workspace_root", "")
    if configured:
        return configured

    key = f"{sandbox['id']}\0{sandbox['url']}"
    cached = _workspace_roots.get(key)
    if cached:
        return cached

    lock = _workspace_root_locks.setdefault(key, asyncio.Lock())
    async with lock:
        cached = _workspace_roots.get(key)
        if cached:
            return cached

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
        root = home.rstrip("/\\") + separator + ".opencode" + separator + "workspaces"
        _workspace_roots[key] = root
        return root


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

    result = await db.execute(select(UserWorkspace).where(UserWorkspace.user_id == user.id))
    workspace = result.scalar_one_or_none()
    if workspace:
        directory = workspace_directory(root, workspace.id)
        if workspace.sandbox_id == sandbox["id"] and workspace.directory == directory:
            await db.commit()
            return sandbox, workspace

    # Do not hold the read transaction while waiting for the process-local lock.
    await db.commit()

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


def _command_payload(command: str, args: list[str], cwd: str, marker: str) -> str:
    payload = json.dumps(
        {"command": command, "args": args, "cwd": cwd, "marker": marker},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return base64.b64encode(payload).decode()


def _parse_command_output(output: str, marker: str) -> tuple[int, str] | None:
    match = re.search(rf"(?:\r?\n)?{re.escape(marker)}(-?\d+)(?:\r?\n)?", output)
    if not match:
        return None
    return int(match.group(1)), output[: match.start()] + output[match.end() :]


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
    marker = f"__OPENCODE_COMMAND_EXIT_{uuid.uuid4().hex}__"
    try:
        async with httpx.AsyncClient(timeout=10.0, auth=auth) as client:
            response = await client.post(
                f"{sandbox['url']}/pty",
                params={"directory": directory},
                json={
                    "command": "node",
                    "args": ["-e", _command_runner_script, _command_payload(command, args, cwd, marker)],
                    "cwd": cwd,
                    "title": title,
                },
            )
            response.raise_for_status()
            info = response.json()
            pty_id = info.get("id")
            if not isinstance(pty_id, str) or not pty_id:
                raise ValueError("Sandbox did not return a PTY id")

            scheme = "wss" if sandbox["url"].startswith("https://") else "ws"
            base_url = sandbox["url"].split("://", 1)[-1].rstrip("/")
            query = {"directory": directory, "cursor": "0"}
            if auth:
                query["auth_token"] = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
            websocket = await asyncio.wait_for(
                connect(f"{scheme}://{base_url}/pty/{pty_id}/connect?{urlencode(query)}"),
                timeout=5.0,
            )
            completed = asyncio.get_running_loop().create_future()

            async def receive_output() -> None:
                try:
                    async for message in websocket:
                        if isinstance(message, bytes):
                            if message.startswith(b"\x00"):
                                continue
                            text = message.decode("utf-8", errors="replace")
                        else:
                            text = message
                        output.append(text)
                        parsed = _parse_command_output("".join(output), marker)
                        if parsed and not completed.done():
                            completed.set_result(parsed)
                except WebSocketException as exc:
                    if not completed.done():
                        completed.set_exception(exc)
                finally:
                    if not completed.done():
                        completed.set_exception(RuntimeError("Sandbox PTY closed before command completion"))

            receiver = asyncio.create_task(receive_output())
            await websocket.send("\r")
            try:
                exit_code, cleaned_output = await asyncio.wait_for(completed, timeout=timeout_seconds)
            except asyncio.TimeoutError as exc:
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail="Sandbox command timed out",
                ) from exc
            return exit_code, cleaned_output if capture_output else ""
    except HTTPException:
        raise
    except (httpx.HTTPError, WebSocketException, OSError, RuntimeError, ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to execute command in the sandbox",
        ) from exc
    finally:
        if receiver:
            receiver.cancel()
            with suppress(asyncio.CancelledError):
                await receiver
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


def _validate_upload_relative_path(relative_path: str) -> str:
    if "\x00" in relative_path or "\\" in relative_path:
        raise ValueError("Invalid sandbox upload path")
    path = PurePosixPath(relative_path)
    if (
        not relative_path
        or path.is_absolute()
        or any(part in {"", ".", ".."} or ":" in part for part in path.parts)
        or str(path) != relative_path
    ):
        raise ValueError("Invalid sandbox upload path")
    return relative_path


async def upload_sandbox_file(
    sandbox: dict,
    directory: str,
    relative_path: str,
    source: BinaryIO | bytes,
    size: int,
    sha256: str,
    timeout_seconds: float = 120.0,
) -> str:
    """Stream one file through an authenticated sandbox PTY and return its remote file URL."""
    relative_path = _validate_upload_relative_path(relative_path)
    if size < 0 or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("Invalid sandbox upload metadata")

    auth = sandbox_auth(sandbox)
    pty_id: str | None = None
    websocket = None
    receiver: asyncio.Task | None = None
    events: asyncio.Queue[dict] = asyncio.Queue()
    marker = f"__U{uuid.uuid4().hex[:12]}__"
    transfer_id = uuid.uuid4().hex
    temporary_path = str(
        PurePosixPath(relative_path).parent / f".upload-{transfer_id}.tmp"
    )
    protocol_buffer = ""
    uploaded = False

    async def next_event() -> dict:
        try:
            event = await asyncio.wait_for(events.get(), timeout=timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Sandbox upload timed out",
            ) from exc
        if event.get("type") == "transport_error":
            raise RuntimeError(event.get("detail", "Sandbox upload connection closed"))
        if event.get("type") == "error":
            raise RuntimeError(event.get("detail", "Sandbox rejected the upload"))
        return event

    try:
        async with httpx.AsyncClient(timeout=10.0, auth=auth) as client:
            response = await client.post(
                f"{sandbox['url']}/pty",
                params={"directory": directory},
                json={
                    "command": "node",
                    "args": [
                        "-e",
                        _upload_receiver_script,
                        directory,
                        relative_path,
                        str(size),
                        sha256,
                        marker,
                        transfer_id,
                    ],
                    "cwd": directory,
                    "title": f"Upload {PurePosixPath(relative_path).name}",
                },
            )
            response.raise_for_status()
            info = response.json()
            pty_id = info.get("id")
            if not isinstance(pty_id, str) or not pty_id:
                raise ValueError("Sandbox did not return a PTY id")

        scheme = "wss" if sandbox["url"].startswith("https://") else "ws"
        base_url = sandbox["url"].split("://", 1)[-1].rstrip("/")
        query = {"directory": directory, "cursor": "0"}
        if auth:
            query["auth_token"] = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        websocket = await asyncio.wait_for(
            connect(f"{scheme}://{base_url}/pty/{pty_id}/connect?{urlencode(query)}"),
            timeout=5.0,
        )

        async def receive_events() -> None:
            nonlocal protocol_buffer
            try:
                async for message in websocket:
                    if isinstance(message, bytes):
                        if message.startswith(b"\x00"):
                            continue
                        text = message.decode("utf-8", errors="replace")
                    else:
                        text = message
                    protocol_buffer += text
                    while True:
                        start = protocol_buffer.find(marker)
                        if start < 0:
                            protocol_buffer = protocol_buffer[-max(len(marker), 4096):]
                            break
                        end = protocol_buffer.find("\n", start + len(marker))
                        if end < 0:
                            protocol_buffer = protocol_buffer[start:]
                            break
                        payload = protocol_buffer[start + len(marker):end]
                        protocol_buffer = protocol_buffer[end + 1:]
                        event = _parse_upload_event(payload)
                        if event is not None:
                            events.put_nowait(event)
            except WebSocketException as exc:
                events.put_nowait({"type": "transport_error", "detail": str(exc)})
            finally:
                events.put_nowait(
                    {"type": "transport_error", "detail": "Sandbox PTY closed during upload"}
                )

        receiver = asyncio.create_task(receive_events())
        ready = await next_event()
        if ready.get("type") != "ready":
            raise RuntimeError("Sandbox did not initialize the upload receiver")

        if isinstance(source, bytes):
            source_view = memoryview(source)
            offset = 0

            def read_chunk() -> bytes:
                nonlocal offset
                chunk = bytes(source_view[offset:offset + _upload_chunk_bytes])
                offset += len(chunk)
                return chunk
        else:
            source.seek(0)

            def read_chunk() -> bytes:
                return source.read(_upload_chunk_bytes)

        sent = 0
        sequence = 0
        while chunk := read_chunk():
            sent += len(chunk)
            if sent > size:
                raise ValueError("Upload source is larger than its declared size")
            await websocket.send(
                json.dumps(
                    {
                        "type": "chunk",
                        "seq": sequence,
                        "data": base64.b64encode(chunk).decode(),
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )
            acknowledgement = await next_event()
            if acknowledgement.get("type") != "ack" or acknowledgement.get("seq") != sequence:
                raise RuntimeError("Invalid sandbox upload acknowledgement")
            sequence += 1
        if sent != size:
            raise ValueError("Upload source is smaller than its declared size")

        await websocket.send(
            json.dumps({"type": "finish", "seq": sequence}, separators=(",", ":")) + "\n"
        )
        url_parts: list[str] = []
        while True:
            completed = await next_event()
            if completed.get("type") == "url" and isinstance(completed.get("data"), str):
                url_parts.append(completed["data"])
                continue
            break
        url = "".join(url_parts)
        if completed.get("type") != "done" or not url.startswith("file:"):
            raise RuntimeError("Sandbox returned an invalid upload result")
        uploaded = True
        return url
    except HTTPException:
        raise
    except (httpx.HTTPError, WebSocketException, OSError, RuntimeError, ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to upload file to the sandbox",
        ) from exc
    finally:
        if receiver:
            receiver.cancel()
            with suppress(asyncio.CancelledError):
                await receiver
        if websocket:
            with suppress(Exception):
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
        if not uploaded and pty_id:
            with suppress(Exception):
                await delete_sandbox_files(sandbox, directory, [temporary_path])


async def replace_sandbox_file(
    sandbox: dict,
    directory: str,
    relative_path: str,
    source: BinaryIO | bytes,
    size: int,
    sha256: str,
    expected_sha256: str,
) -> None:
    """Replace a workspace file only when its current hash matches the caller's revision."""
    relative_path = _validate_upload_relative_path(relative_path)
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("Invalid expected sandbox file hash")
    lock_key = f"{sandbox['id']}\0{directory}\0{relative_path}"
    lock = _replace_file_locks.setdefault(lock_key, asyncio.Lock())
    async with lock:
        transfer_id = uuid.uuid4().hex
        parent = PurePosixPath(relative_path).parent
        temporary_path = str(parent / f".replace-{transfer_id}.tmp")
        moved = False
        try:
            await upload_sandbox_file(
                sandbox=sandbox,
                directory=directory,
                relative_path=temporary_path,
                source=source,
                size=size,
                sha256=sha256,
            )
            exit_code, _ = await run_sandbox_command_capture(
                sandbox=sandbox,
                directory=directory,
                command="node",
                args=[
                    "-e",
                    _replace_uploaded_file_script,
                    directory,
                    temporary_path,
                    relative_path,
                    expected_sha256,
                    transfer_id,
                ],
                cwd=directory,
                title=f"Save {PurePosixPath(relative_path).name}",
                timeout_seconds=30.0,
                capture_output=False,
            )
            if exit_code == 3:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Workspace file changed since it was opened",
                )
            if exit_code != 0:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Unable to replace the workspace file",
                )
            moved = True
        finally:
            if not moved:
                with suppress(Exception):
                    await delete_sandbox_files(sandbox, directory, [temporary_path])


async def delete_sandbox_files(sandbox: dict, directory: str, relative_paths: list[str]) -> None:
    paths = [_validate_upload_relative_path(path) for path in relative_paths]
    if not paths:
        return
    encoded_paths = base64.b64encode(
        json.dumps(paths, ensure_ascii=False, separators=(",", ":")).encode()
    ).decode()
    await run_sandbox_command(
        sandbox=sandbox,
        directory=directory,
        command="node",
        args=["-e", _delete_uploaded_files_script, directory, encoded_paths],
        cwd=directory,
        title="Clean up failed upload",
        timeout_seconds=30.0,
    )


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
