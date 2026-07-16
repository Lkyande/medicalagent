# Your Platform API

本文档描述 FastAPI 网关对前端开放的接口。开发环境默认后端地址为 `http://localhost:8000`，业务 API 前缀为 `/api`，OpenCode 代理前缀为 `/opencode`。

FastAPI 自动文档：

- Swagger UI: `GET /docs`
- OpenAPI JSON: `GET /openapi.json`

## 认证方式

除健康检查和注册/登录外，所有接口都需要 JWT：

```http
Authorization: Bearer <token>
```

请求体和 JSON 响应默认使用 `Content-Type: application/json`。下载接口返回文件流，并通过 `Content-Disposition` 提供文件名。

## 错误格式

FastAPI 业务错误通常返回：

```json
{
  "detail": "错误说明"
}
```

常见状态码：

| 状态码 | 含义 |
|------|------|
| `400` | 请求参数非法，例如路径不安全或邮箱已注册 |
| `401` | 未登录、Token 无效或登录密码错误 |
| `404` | 资源不存在，或访问了不属于当前用户的会话 |
| `409` | 状态冲突，例如目录非空或会话归属冲突 |
| `413` | 工作目录归档超过限制 |
| `422` | 请求体或查询参数校验失败 |
| `502` | 沙盒服务不可用、返回异常或命令执行失败 |
| `503` | 未配置可用 OpenCode 沙盒 |
| `504` | 沙盒命令超时 |

## 健康检查

### `GET /api/health`

无需认证，用于探活。

响应：

```json
{
  "status": "ok"
}
```

## 认证 API

### `POST /api/auth/register`

注册用户并返回 JWT。

请求体：

```json
{
  "email": "user@example.com",
  "password": "your-password"
}
```

响应：

```json
{
  "token": "eyJ...",
  "user": {
    "id": "9f8e7d...",
    "email": "user@example.com",
    "sandbox_id": null
  }
}
```

错误：

| 状态码 | 场景 |
|------|------|
| `400` | 邮箱已注册 |
| `422` | 邮箱格式或请求体非法 |

### `POST /api/auth/login`

使用邮箱和密码登录。

请求体：

```json
{
  "email": "user@example.com",
  "password": "your-password"
}
```

响应：

```json
{
  "token": "eyJ...",
  "user": {
    "id": "9f8e7d...",
    "email": "user@example.com",
    "sandbox_id": "a"
  }
}
```

错误：

| 状态码 | 场景 |
|------|------|
| `401` | 邮箱不存在或密码错误 |

### `GET /api/auth/me`

获取当前用户信息。

响应：

```json
{
  "id": "9f8e7d...",
  "email": "user@example.com",
  "sandbox_id": "a"
}
```

## 沙盒分配

### `POST /api/dispatch`

为当前用户分配或获取已有沙盒，并确保用户工作目录存在。首次调用会按已分配用户数选择负载最低的沙盒。

响应：

```json
{
  "sandboxId": "a",
  "proxyBase": "/opencode",
  "workspace": "/workspace/users/2f1c..."
}
```

说明：

- `sandboxId` 是平台内部沙盒 ID，不是沙盒 URL。
- `proxyBase` 是前端 OpenCode SDK 使用的代理基路径。
- `workspace` 是当前用户在沙盒内的真实工作目录。

## 沙盒管理

### `GET /api/sandboxes`

列出已配置沙盒 ID。

响应：

```json
[
  { "id": "a" },
  { "id": "b" }
]
```

### `GET /api/sandboxes/{sandbox_id}/status`

检查沙盒健康状态。当前实现会返回沙盒 URL，生产环境建议改为管理员接口或移除 `url` 字段。

响应：

```json
{
  "id": "a",
  "url": "http://10.0.0.11:4096",
  "healthy": true
}
```

沙盒不存在时返回：

```json
{
  "id": "missing",
  "url": "",
  "healthy": false
}
```

## 工作目录 API

工作目录 API 会自动为当前用户创建或复用工作目录。所有 `path` 参数都必须是相对路径，并且不能包含：

- 空字节
- 反斜杠 `\`
- 绝对路径
- `..` 路径逃逸
- 指向工作目录外部的符号链接

### `GET /api/workspace`

获取当前用户工作目录信息。

响应：

```json
{
  "id": "2f1c...",
  "sandboxId": "a",
  "directory": "/workspace/users/2f1c..."
}
```

### `GET /api/workspace/files?path=`

列出指定目录下的直接子项。`path` 为空时表示工作目录根目录。

响应：

```json
[
  {
    "name": "src",
    "path": "src",
    "type": "directory",
    "ignored": false
  },
  {
    "name": "README.md",
    "path": "README.md",
    "type": "file",
    "ignored": false
  }
]
```

说明：

- 只返回当前目录的直接子项，不递归。
- 响应会过滤非法节点和不在目标目录下的节点。
- 后端对同一目录有短时间缓存，删除等操作会强制刷新。

### `GET /api/workspace/content?path=<file>`

读取单个文件内容。

文本文件响应示例：

```json
{
  "type": "text",
  "content": "# README\n",
  "mimeType": "text/markdown"
}
```

二进制文件响应示例：

```json
{
  "type": "binary",
  "content": "iVBORw0KGgo=",
  "encoding": "base64",
  "mimeType": "image/png"
}
```

错误：

| 状态码 | 场景 |
|------|------|
| `400` | 路径非法或目标不是合法文件 |
| `404` | 文件不存在 |
| `502` | 沙盒返回非法响应 |

### `GET /api/workspace/download?path=<file>`

下载单个文件。响应体是文件内容，响应头包含：

```http
Content-Disposition: attachment; filename="README.md"; filename*=UTF-8''README.md
```

### `GET /api/workspace/archive`

下载整个用户工作目录 ZIP。

限制：

| 限制 | 当前值 |
|------|------|
| 最大文件数 | `2000` |
| 最大总大小 | `200 MiB` |
| 最大目录深度 | `24` |

超过限制返回 `413`。

### `DELETE /api/workspace/files`

删除单个文件或空目录。

请求体：

```json
{
  "path": "src/old-file.ts"
}
```

响应：

```json
{
  "deleted": "src/old-file.ts",
  "type": "file"
}
```

说明：

- 目录删除不递归。
- 非空目录返回 `409`。
- 删除操作会先确认目标属于当前工作目录。

## OpenCode 代理 API

### `ALL /opencode/{path}`

代理到当前用户所属的 OpenCode 沙盒。代理会注入沙盒 Basic Auth，并覆盖请求中的 `directory` 或 `workspace` 参数，防止前端指定其他目录。

认证：需要 JWT。

开放路径：

| OpenCode 路径 | 说明 |
|------|------|
| `session` | 会话列表、创建会话 |
| `session/status` | 会话状态 |
| `session/{id}` 及其子路径 | 当前用户已归属会话的操作，例如发送 prompt |
| `provider` | Provider 列表 |
| `agent` | Agent 列表 |
| `app/agents` | App Agent 列表 |
| `global/config` | 全局配置 |
| `global/event` | SSE 事件流 |
| `global/health` | 沙盒健康检查 |
| `path` | 当前用户路径信息 |
| `project` | 项目列表 |

显式禁用：

| OpenCode 路径 | 行为 |
|------|------|
| `session/{id}/shell` | 返回 `404` |

代理行为：

- `GET /opencode/session` 只返回 `directory` 等于当前用户工作目录的会话，并记录会话归属。
- `POST /opencode/session` 创建成功后记录会话归属。
- `GET /opencode/session/status` 只返回当前用户拥有的会话状态。
- `GET /opencode/project` 只返回 `worktree` 等于当前用户工作目录的项目。
- `GET /opencode/path` 将 `home`、`state`、`config`、`worktree` 和 `directory` 都改写为当前用户工作目录。
- `GET /opencode/global/event` 只透传当前工作目录相关事件，以及 `server.connected`、`server.heartbeat`、`global.disposed`。
- 创建或更新会话时，代理会注入 `external_directory` deny 权限，降低 Agent 访问工作目录外路径的风险。

示例：

```bash
curl http://localhost:8000/opencode/session \
  -H "Authorization: Bearer your-jwt-token"
```

## 业务 API

### 助手目录

`GET /api/assistants` 返回平台发布的助手能力配置。助手不是独立 OpenCode Agent，而是在 `build` 基础 Agent 上叠加系统提示、Skill 和工具。

```json
[
  {
    "id": "clinical-assistant",
    "name": "诊疗助手",
    "description": "基于已审核医学资料，为医生提供带来源和风险提示的临床辅助决策建议。",
    "version": "1.0.0",
    "baseAgent": "build",
    "systemPrompt": "本会话启用了诊疗助手能力……",
    "skills": ["clinical-decision-support", "disease-guidelines"],
    "tools": ["murtagh-medical"]
  }
]
```

`POST /api/assistants/{assistant_id}/install` 在当前用户工作目录启用能力所需的入口 Skill 和 MCP 配置。客户端不能提供来源路径、目标路径或文件内容，也不会创建新的 OpenCode Agent。

```json
{
  "id": "clinical-assistant",
  "name": "诊疗助手",
    "description": "基于已审核医学资料，为医生提供带来源和风险提示的临床辅助决策建议。",
    "version": "1.0.0",
    "baseAgent": "build",
    "systemPrompt": "本会话启用了诊疗助手能力……",
    "skills": ["clinical-decision-support", "disease-guidelines"],
    "tools": ["murtagh-medical"],
    "enabled": true,
    "changed": false,
    "mcpStatus": "connected",
    "requiresNewSession": false
}
```

启用内容包括两个入口 Skill 和远程 `murtagh-medical` MCP 配置。前端发送消息时仍使用 `agent: "build"`，并通过 `system` 注入诊疗能力规则。安装是幂等的：`changed` 仅在 Skill、清单或 MCP 配置实际变化时为 `true`，此时 `requiresNewSession` 也为 `true`，后端只刷新当前工作目录的 OpenCode 实例。配置未变化时不会刷新实例，避免中止正在进行的会话。

接口返回成功前还会查询沙盒 OpenCode 的 `/mcp` 状态；只有 `murtagh-medical` 为 `connected` 时才返回 `mcpStatus: "connected"`。MCP 不可达时返回 `502`，不会让前端误显示“已启用”。

入口 Skill 的唯一发布源为 `backend/app/assistant_catalog/<assistant_id>/skills/`。根目录 `.opencode/skills/` 下的同名文件是本地开发镜像，不是安装源；测试会校验两者内容一致。`MURTAGH_KNOWLEDGE_ROOT` 仅包含莫塔路由索引、注册表和审核后的 Markdown 正文，不会复制到用户沙盒。

### `GET /api/business/hello`

示例业务接口。

响应：

```json
{
  "message": "Hello user@example.com",
  "userId": "9f8e7d..."
}
```

新增业务接口时，建议在 `backend/app/routers/business.py` 添加路由，并在 `frontend/src/api/backend.ts` 增加对应 client 方法。

## 环境变量

后端读取 `backend/.env`。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `JWT_SECRET` | `dev-secret` | JWT 签名密钥，生产必须修改 |
| `JWT_ALGORITHM` | `HS256` | JWT 签名算法 |
| `JWT_EXPIRE_DAYS` | `7` | JWT 过期天数 |
| `DATABASE_URL` | `sqlite+aiosqlite:///./dev.db` | SQLAlchemy async 连接串；`.env.example` 使用 MySQL |
| `CORS_ORIGINS` | `http://localhost:4444,http://localhost:5173` | 允许跨域来源，逗号分隔 |
| `SANDBOX_WORKSPACE_ROOT` | 空 | 所有沙盒默认工作目录根路径；为空时通过 `/path` 自动探测 |
| `MURTAGH_MCP_URL` | 空 | 沙盒可访问的莫塔医学远程 MCP 地址；不能使用远程沙盒自身的 `localhost` |
| `MURTAGH_KNOWLEDGE_ROOT` | 仓库内莫塔 skill 目录 | 莫塔索引、注册表和审核后 Markdown 的只读目录 |
| `SANDBOX_<ID>_URL` | - | OpenCode 沙盒 URL |
| `SANDBOX_<ID>_PASSWORD` | 空 | OpenCode 沙盒 Basic Auth 密码 |
| `SANDBOX_<ID>_USERNAME` | `opencode` | OpenCode 沙盒 Basic Auth 用户名 |
| `SANDBOX_<ID>_WORKSPACE_ROOT` | 全局值 | 单个沙盒的工作目录根路径覆盖 |

前端读取 `frontend/.env`。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `VITE_BACKEND_API_URL` | `/api` | 业务 API 基地址 |
| `VITE_OPENCODE_PROXY_BASE` | `/opencode` | OpenCode 代理基地址 |

## 调用顺序

典型前端流程：

1. 调用 `POST /api/auth/register` 或 `POST /api/auth/login` 获取 JWT。
2. 保存 JWT，并在后续请求中添加 `Authorization: Bearer <token>`。
3. 调用 `POST /api/dispatch` 获取沙盒分配和工作目录。
4. 使用 `/opencode` 作为 OpenCode SDK base URL。
5. 使用 `/api/workspace/**` 读取、下载或删除当前用户工作目录内容。
6. 使用 `/api/business/**` 调用平台自定义业务接口。
