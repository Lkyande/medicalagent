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
    mcp_servers: tuple[tuple[str, str, str], ...]


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
    mcp_servers=(("murtagh-medical", "murtagh_mcp_url", "murtagh_mcp_timeout_ms"),),
)

EDUCATION_ASSISTANT = AssistantDefinition(
    id="education-assistant",
    name="宣教助手",
    description="面向公众生成医学科普宣传文案、配图和微信公众号排版预览。",
    version="1.6.0",
    base_agent="build",
    system_prompt=(
        "本会话启用了宣教助手能力。你仍然是 Build 基础 Agent，但必须按照医学科普宣教模式工作。\n\n"
        "1. 先判断用户是否给出具体医学主题。若只有医院科普宣传、健康宣教等宽泛意图以及受众、渠道或图片风格，必须先询问是否需要结合当前热点素材选题，不得立即搜索热点或创建预览。\n"
        "2. 生成微信公众号科普预览时必须加载 health-education-content 技能，并按其要求调用 education-content 工具。\n"
        "3. 用户明确要求结合热点，或对上一条询问回答需要时，先调用 smart-market-hot-topics 搜索；用户回答不需要时，不得搜索热点，应询问具体主题或提供常规宣教主题供选择。\n"
        "4. 用户已给出具体疾病、症状、专科或健康主题且未要求热点时，直接按主题生成，不得强制搜索或重复询问热点。热点结果只用于传播背景，不得把热榜观点当作医学证据。\n"
        "5. 必须先调用 education_create_preview，并在拿到 job_id 后调用 education_wait_for_preview；后续只能使用同一 job_id，禁止重复创建任务。\n"
        "6. pending、running、timed_out 及 MCP -32001/Request timed out 都不代表任务失败；必须从工具输入恢复同一 job_id 继续查询，禁止显示任务执行失败或重新创建。只有 completed 或明确 failed 才能结束，completed 时必须提供 editable_html_url。\n"
        "7. 不得编造患病率、疗效、诊断标准、指南结论、机构背书或来源。资料不足时使用审慎的通用表述并标记待审核。\n"
        "8. 不针对具体患者作诊断或处方；个案诊疗问题应建议进入诊疗助手或由医生评估。\n"
        "9. 出现急症危险信号时优先提示立即就医，不得将普通科普内容替代紧急处置。\n"
        "10. 当前只允许生成和查看预览，不得创建微信草稿、提交发布或删除内容。"
    ),
    skills=("health-education-content",),
    tools=("education-content", "smart-market-hot-topics"),
    files=("skills/health-education-content/SKILL.md",),
    mcp_servers=(
        ("education-content", "education_mcp_url", "education_mcp_timeout_ms"),
        ("smart-market-hot-topics", "hot_topics_mcp_url", "hot_topics_mcp_timeout_ms"),
    ),
)

ASSISTANTS = {
    CLINICAL_ASSISTANT.id: CLINICAL_ASSISTANT,
    EDUCATION_ASSISTANT.id: EDUCATION_ASSISTANT,
}
_install_locks: dict[str, asyncio.Lock] = {}
_reload_pending: set[str] = set()
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

// Remove obsolete standalone agents from earlier profile versions.
if (["clinical-assistant", "education-assistant"].includes(payload.id)) {
  const obsoleteAgent = path.join(configDir, "agent", payload.id + ".md");
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
for (const server of payload.mcpServers) {
  const desiredMcp = {
    type: "remote",
    url: server.url,
    enabled: true,
    timeout: server.timeoutMs
  };
  if (JSON.stringify(config.mcp[server.name]) !== JSON.stringify(desiredMcp)) {
    config.mcp[server.name] = desiredMcp;
    changed = true;
  }
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
            "mcpServers": [
                {
                    "name": name,
                    "url": getattr(settings, url_setting).rstrip("/") + "/",
                    "timeoutMs": getattr(settings, timeout_setting),
                }
                for name, url_setting, timeout_setting in assistant.mcp_servers
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return base64.b64encode(payload).decode()


async def install_assistant(sandbox: dict, workspace: UserWorkspace, assistant: AssistantDefinition) -> bool:
    missing_servers = [
        name
        for name, url_setting, _timeout_setting in assistant.mcp_servers
        if not getattr(settings, url_setting)
    ]
    if missing_servers:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"MCP URL is not configured for: {', '.join(missing_servers)}",
        )
    lock_key = f"{sandbox['id']}\0{workspace.directory}"
    lock = _install_locks.setdefault(lock_key, asyncio.Lock())
    async with lock:
        if await _workspace_has_active_sessions(sandbox, workspace.directory):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Assistant update is waiting for active workspace tasks to finish",
            )
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
            _reload_pending.add(lock_key)
        requires_reload = lock_key in _reload_pending
        if requires_reload:
            if await _workspace_has_active_sessions(sandbox, workspace.directory):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Assistant update is waiting for active workspace tasks to finish",
                )
            await _dispose_workspace_instance(sandbox, workspace.directory)
            _reload_pending.discard(lock_key)
        for server_name in assistant.tools:
            await _wait_for_mcp_connection(sandbox, workspace.directory, server_name)
        return changed or requires_reload


async def _workspace_has_active_sessions(sandbox: dict, directory: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=10.0, auth=sandbox_auth(sandbox)) as client:
            response = await client.get(
                f"{sandbox['url']}/session/status",
                params={"directory": directory},
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to verify whether the workspace is idle before updating the assistant",
        ) from exc
    statuses = payload.values() if isinstance(payload, dict) else payload if isinstance(payload, list) else []
    return any(isinstance(item, dict) and item.get("type") != "idle" for item in statuses)


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
