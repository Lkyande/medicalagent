import base64
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import httpx
from fastapi import HTTPException, status

from app.config import settings
from app.models import UserWorkspace
from app.sandbox.manager import run_sandbox_command_capture, sandbox_auth


CATALOG_ROOT = Path(__file__).resolve().parent.parent / "assistant_catalog"


@dataclass(frozen=True)
class AssistantDefinition:
    id: str
    name: str
    description: str
    version: str
    base_agent: str
    system_prompt: str
    skills: tuple[str, ...]
    tools: tuple[str, ...]
    files: tuple[str, ...]


CLINICAL_ASSISTANT = AssistantDefinition(
    id="clinical-assistant",
    name="诊疗助手",
    description="基于已审核医学资料，为医生提供带来源和风险提示的临床辅助决策建议。",
    version="1.0.0",
    base_agent="build",
    system_prompt=(
        "本会话启用了诊疗助手能力。你仍然是 Build 基础 Agent，但必须按照临床辅助决策模式工作。\n\n"
        "1. 区分病例事实、患者自述、临床推断、建议和未知信息，不得补造资料。\n"
        "2. 涉及疾病评估、诊断标准、危险信号、禁忌证或治疗方向时，加载 "
        "clinical-decision-support 和 disease-guidelines 技能。\n"
        "3. 按 disease-guidelines 的要求调用 murtagh_route，再调用 murtagh_get_skills 获取完整审核内容；"
        "不得只依据路由元数据或模型通用知识生成已覆盖的疾病专项意见。\n"
        "4. 诊疗分析应包含鉴别诊断、支持与反对证据、待补充检查、治疗方向、禁忌和不确定性。\n"
        "5. 出现急症危险信号时，优先提示立即人工评估或升级处置。\n"
        "6. 输出仅用于临床辅助决策，不构成最终诊断或处方，不授权联系患者或修改正式病历。"
    ),
    skills=("clinical-decision-support", "disease-guidelines"),
    tools=("murtagh-medical",),
    files=(
        "skills/clinical-decision-support/SKILL.md",
        "skills/disease-guidelines/SKILL.md",
    ),
)

ASSISTANTS = {CLINICAL_ASSISTANT.id: CLINICAL_ASSISTANT}
_install_locks: dict[str, asyncio.Lock] = {}
_mcp_connect_attempts = 10
_mcp_connect_delay = 0.5

_install_script = r"""
const fs = require("fs");
const path = require("path");
const root = path.resolve(process.argv[1]);
const payload = JSON.parse(Buffer.from(process.argv[2], "base64").toString("utf8"));
const configDir = path.join(root, ".opencode");
fs.mkdirSync(configDir, { recursive: true });
let changed = false;

// Remove the obsolete standalone agent from earlier profile versions.
if (payload.id === "clinical-assistant") {
  const obsoleteAgent = path.join(configDir, "agent", "clinical-assistant.md");
  if (fs.existsSync(obsoleteAgent)) {
    fs.rmSync(obsoleteAgent, { force: true });
    changed = true;
  }
}

for (const item of payload.files) {
  const target = path.resolve(configDir, item.path);
  if (!target.startsWith(configDir + path.sep)) throw new Error("Invalid assistant package path");
  const content = Buffer.from(item.content, "base64");
  if (fs.existsSync(target) && fs.readFileSync(target).equals(content)) continue;
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const temp = target + ".tmp";
  fs.writeFileSync(temp, content);
  fs.rmSync(target, { force: true });
  fs.renameSync(temp, target);
  changed = true;
}

const jsonConfig = path.join(configDir, "opencode.json");
const jsoncConfig = path.join(configDir, "opencode.jsonc");
if (!fs.existsSync(jsonConfig) && fs.existsSync(jsoncConfig)) {
  throw new Error("Cannot safely merge MCP configuration into opencode.jsonc");
}
let config = { $schema: "https://opencode.ai/config.json" };
if (fs.existsSync(jsonConfig)) config = JSON.parse(fs.readFileSync(jsonConfig, "utf8"));
config.$schema = config.$schema || "https://opencode.ai/config.json";
config.mcp = config.mcp || {};
const desiredMcp = {
  type: "remote",
  url: payload.mcpUrl,
  enabled: true,
  timeout: payload.mcpTimeoutMs
};
if (JSON.stringify(config.mcp["murtagh-medical"]) !== JSON.stringify(desiredMcp)) {
  config.mcp["murtagh-medical"] = desiredMcp;
  changed = true;
}
if (changed || !fs.existsSync(jsonConfig)) {
  const configTemp = jsonConfig + ".tmp";
  fs.writeFileSync(configTemp, JSON.stringify(config, null, 2) + "\n");
  fs.rmSync(jsonConfig, { force: true });
  fs.renameSync(configTemp, jsonConfig);
  changed = true;
}
process.exitCode = changed ? 20 : 0;
""".strip()


def get_assistant(assistant_id: str) -> AssistantDefinition:
    assistant = ASSISTANTS.get(assistant_id)
    if not assistant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assistant not found")
    return assistant


def _package_payload(assistant: AssistantDefinition) -> str:
    package_root = CATALOG_ROOT / assistant.id
    files = []
    for relative in assistant.files:
        source = package_root / relative
        if not source.is_file():
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Assistant package is incomplete: {relative}",
            )
        files.append(
            {
                "path": relative.replace("\\", "/"),
                "content": base64.b64encode(source.read_bytes()).decode(),
            }
        )
    manifest = json.dumps(
        {"id": assistant.id, "version": assistant.version},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    files.append(
        {
            "path": f"assistant-profiles/{assistant.id}.json",
            "content": base64.b64encode(manifest).decode(),
        }
    )
    payload = json.dumps(
        {
            "id": assistant.id,
            "files": files,
            "mcpUrl": settings.murtagh_mcp_url,
            "mcpTimeoutMs": settings.murtagh_mcp_timeout_ms,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return base64.b64encode(payload).decode()


async def install_assistant(sandbox: dict, workspace: UserWorkspace, assistant: AssistantDefinition) -> bool:
    if not settings.murtagh_mcp_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MURTAGH_MCP_URL is not configured",
        )
    lock_key = f"{sandbox['id']}\0{workspace.directory}\0{assistant.id}"
    lock = _install_locks.setdefault(lock_key, asyncio.Lock())
    async with lock:
        code, _ = await run_sandbox_command_capture(
            sandbox,
            workspace.directory,
            "node",
            ["-e", _install_script, workspace.directory, _package_payload(assistant)],
            cwd=workspace.directory,
            title=f"Enable {assistant.name}",
            timeout_seconds=30.0,
            capture_output=False,
        )
        if code not in {0, 20}:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Unable to enable the assistant capabilities in the sandbox workspace",
            )
        changed = code == 20
        if changed:
            await _dispose_workspace_instance(sandbox, workspace.directory)
        for server_name in assistant.tools:
            await _wait_for_mcp_connection(sandbox, workspace.directory, server_name)
        return changed


async def _dispose_workspace_instance(sandbox: dict, directory: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=15.0, auth=sandbox_auth(sandbox)) as client:
            response = await client.post(
                f"{sandbox['url']}/instance/dispose",
                params={"directory": directory},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Assistant installed, but OpenCode could not reload the workspace configuration",
        ) from exc


async def _wait_for_mcp_connection(sandbox: dict, directory: str, server_name: str) -> None:
    error = "MCP server did not report a status"
    for attempt in range(_mcp_connect_attempts):
        try:
            async with httpx.AsyncClient(timeout=10.0, auth=sandbox_auth(sandbox)) as client:
                response = await client.get(
                    f"{sandbox['url']}/mcp",
                    params={"directory": directory},
                )
                response.raise_for_status()
                status_payload = response.json().get(server_name, {})
                if status_payload.get("status") == "connected":
                    return
                error = status_payload.get("error") or f"status={status_payload.get('status', 'missing')}"
                reconnect = await client.post(
                    f"{sandbox['url']}/mcp/{server_name}/connect",
                    params={"directory": directory},
                )
                reconnect.raise_for_status()
        except httpx.HTTPStatusError as exc:
            error = f"HTTP {exc.response.status_code}: {exc.response.text[:300]}"
        except httpx.RequestError as exc:
            error = f"{exc.__class__.__name__}: {exc}"
        except (ValueError, TypeError) as exc:
            error = str(exc)
        if attempt + 1 < _mcp_connect_attempts:
            await asyncio.sleep(_mcp_connect_delay)
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"Assistant installed, but MCP server {server_name} is unavailable: {error}",
    )
