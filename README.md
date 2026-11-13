# Your Platform

基于 [OpenCode](https://opencode.ai) 的 AI 编码平台，保留原始 Agent 功能，新增自定义业务逻辑。

接口细节见 [API.md](./API.md)。

## 架构

```
浏览器（公网）
  │
  ├── 前端（SolidJS / Vite）
  │     └── 从 OpenCode packages/app + ui + session-ui 拆分而来
  │
  └── FastAPI 网关（公网入口）
        ├── 用户认证（JWT）
        ├── 沙盒分配（最少负载共享 OpenCode 实例）
        ├── 用户工作目录与会话所有权隔离
        ├── 业务 API（/api/business/**）
        └── OpenCode 反向代理（/opencode/** → 沙盒实例）
              │
              │  内网 / Tailscale
              ▼
        OpenCode 沙盒实例池（不暴露公网）
          ├── 沙盒 A: 10.0.0.11:4096
          ├── 沙盒 B: 10.0.0.12:4096
          └── ...
```

### 数据流

1. 用户登录 → FastAPI 验证身份 → 返回 JWT
2. 前端调用 `POST /api/dispatch` → FastAPI 选择沙盒、创建用户工作目录并初始化统一助手能力包
3. 前端 OpenCode SDK 以 `/opencode` 为 baseUrl 发起请求
4. FastAPI 校验会话所有权、覆盖工作目录参数并注入 Basic Auth
5. SSE 事件流（`/opencode/global/event`）按用户工作目录过滤后透传
6. 工作目录树和内容通过 `/api/workspace/**` 安全读取
7. 业务数据通过 `/api/business/**` 独立访问

## 目录结构

```
your-platform/
├── frontend/                  # 前端（SolidJS + Vite）
│   ├── src/
│   │   ├── lib/               # 内联工具函数（从 core/util 拆出）
│   │   │   ├── encode.ts      # base64 / checksum / hash
│   │   │   ├── path.ts        # 路径处理
│   │   │   ├── binary.ts      # 二分查找
│   │   │   ├── retry.ts       # 重试
│   │   │   └── array.ts       # findLast
│   │   ├── api/
│   │   │   └── backend.ts     # 业务 API client
│   │   ├── ui/                # UI 组件库（原 @opencode-ai/ui）
│   │   ├── session-ui/        # Session UI 组件（原 @opencode-ai/session-ui）
│   │   ├── context/           # 前端上下文（server-sdk / server-sync / ...）
│   │   ├── pages/             # 页面（home / session / layout）
│   │   ├── components/        # 通用组件
│   │   ├── i18n/              # 国际化
│   │   ├── entry.tsx          # 入口（默认指向 FastAPI 代理）
│   │   └── env.d.ts           # 环境变量声明
│   ├── public/                # 静态资源
│   ├── vite.config.ts         # Vite 配置
│   ├── vite.js                # Vite 插件（alias / solid / tailwind）
│   ├── index.html
│   ├── package.json
│   ├── .env.example
│   └── Dockerfile
│
├── backend/                   # 后端（FastAPI）
│   ├── app/
│   │   ├── main.py            # 入口 + CORS + OpenCode 代理路由
│   │   ├── config.py          # 配置 + 沙盒映射
│   │   ├── database.py        # SQLAlchemy async 连接
│   │   ├── models.py          # User / SandboxAssignment / UserWorkspace / UserSession 模型
│   │   ├── auth/
│   │   │   ├── jwt_handler.py # JWT 签发 / 验证
│   │   │   └── dependencies.py# get_current_user 依赖
│   │   ├── sandbox/
│   │   │   └── manager.py     # 沙盒分配 / 健康检查
│   │   ├── proxy/
│   │   │   └── opencode_proxy.py  # 反向代理（含 SSE 透传）
│   │   └── routers/
│   │       ├── auth.py        # /api/auth/*
│   │       ├── dispatch.py    # /api/dispatch
│   │       ├── sandbox.py     # /api/sandboxes/*
│   │       ├── workspace.py   # /api/workspace/*
│   │       ├── business.py    # /api/business/*
│   │       └── health.py      # /api/health
│   ├── requirements.txt
│   ├── .env.example
│   └── Dockerfile
│
├── nginx/
│   └── default.conf           # 生产 Nginx 配置（含 SSE 优化）
├── docker-compose.yml         # 容器编排
├── Makefile                   # 开发快捷命令
├── API.md                     # 后端接口文档
└── README.md
```

## 快速开始

### 前置条件

| 组件 | 版本要求 | 说明 |
|------|---------|------|
| Node.js | 22+ | 前端构建 |
| Python | 3.12+ | 后端运行 |
| OpenCode | latest | 沙盒机器上安装 |

### 1. 启动 OpenCode 沙盒

在每台沙盒机器上：

```bash
# 安装 OpenCode
npm install -g opencode-ai@latest

# 生成随机密码
export OPENCODE_SERVER_PASSWORD=$(openssl rand -hex 16)
export OPENCODE_SERVER_USERNAME=opencode

# 启动 headless server
opencode serve --hostname 0.0.0.0 --port 4096

# 记录密码，稍后配置到 FastAPI
echo "Password: $OPENCODE_SERVER_PASSWORD"
```

常驻运行（systemd）：

```ini
# /etc/systemd/system/opencode-sandbox.service
[Unit]
Description=OpenCode Sandbox Server
After=network.target

[Service]
Type=simple
Environment=OPENCODE_SERVER_PASSWORD=你的随机密码
Environment=OPENCODE_SERVER_USERNAME=opencode
ExecStart=/usr/bin/opencode serve --hostname 0.0.0.0 --port 4096
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 2. 启动 FastAPI 后端

```bash
cd backend

# 安装依赖
python -m pip install -r requirements.txt

# 配置环境
cp .env.example .env
# 编辑 .env，填入数据库、沙盒 IP 和密码
#   DATABASE_URL=mysql+aiomysql://root:123456@localhost:3320/your_platform
#   SANDBOX_A_URL=http://10.0.0.11:4096
#   SANDBOX_A_PASSWORD=你的沙盒密码
#   JWT_SECRET=你的JWT密钥

# 启动开发服务器。远程沙盒需要访问 MCP，因此不能只监听 127.0.0.1。
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 20140
```

### 3. 启动前端

```bash
cd frontend

# 安装依赖
npm install

# 配置环境（开发环境）
# .env 已预配置：
#   VITE_OPENCODE_PROXY_BASE=http://localhost:20140/opencode
#   VITE_BACKEND_API_URL=http://localhost:20140/api

# 启动开发服务器
npm run dev
```

打开 http://localhost:4444

### 4. 使用 Makefile（可选）

```bash
make dev-backend    # 启动 FastAPI
make dev-frontend   # 启动前端
make build          # Docker 构建
make up             # Docker 启动
make down           # Docker 停止
```

## API 接口

完整请求/响应示例、错误码和代理规则见 [API.md](./API.md)。后端同时提供 FastAPI 自动文档：开发环境打开 `http://localhost:20140/docs`，OpenAPI JSON 为 `http://localhost:20140/openapi.json`。

### 认证

| 路径 | 方法 | 认证 | 说明 |
|------|------|------|------|
| `POST /api/auth/register` | POST | 无 | 邮箱或手机号注册，返回 JWT |
| `POST /api/auth/login` | POST | 无 | 邮箱或手机号登录，返回 JWT |
| `GET /api/auth/me` | GET | JWT | 当前用户信息 |

**注册示例：**

```bash
curl -X POST http://localhost:20140/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"account":"user@example.com","password":"your-password"}'
```

**响应：**

```json
{
  "token": "eyJ...",
  "user": { "id": "...", "email": "user@example.com", "phone": null, "sandbox_id": null }
}
```

### 沙盒管理

| 路径 | 方法 | 认证 | 说明 |
|------|------|------|------|
| `POST /api/dispatch` | POST | JWT | 分配/获取当前用户沙盒 |
| `GET /api/sandboxes` | GET | JWT | 列出可用沙盒（仅返回 id） |
| `GET /api/sandboxes/{id}/status` | GET | JWT | 沙盒健康状态 |

### 工作目录

| 路径 | 方法 | 认证 | 说明 |
|------|------|------|------|
| `GET /api/workspace` | GET | JWT | 当前用户工作目录信息 |
| `GET /api/workspace/files?path=` | GET | JWT | 列出用户工作目录内的文件和空目录 |
| `GET /api/workspace/content?path=...` | GET | JWT | 读取用户工作目录内的文件 |
| `GET /api/workspace/download?path=...` | GET | JWT | 下载单个文件 |
| `GET /api/workspace/archive` | GET | JWT | 下载整个用户工作目录 ZIP |
| `DELETE /api/workspace/files` | DELETE | JWT | 永久删除文件或空目录，请求体为 `{ "path": "..." }` |

工作目录 API 仅接受相对路径，并拒绝绝对路径、反斜杠、`..` 路径逃逸和指向工作目录外的符号链接。目录删除不递归，非空目录返回 `409`。

### OpenCode 代理

| 路径 | 方法 | 认证 | 说明 |
|------|------|------|------|
| `/opencode/{path}` | ALL | JWT | 反向代理到用户沙盒 |

代理不是全量暴露 OpenCode API。当前仅开放会话、Provider、Agent、Project、Path、Global 配置/事件/健康检查等 allowlist 路径；`/opencode/session/{id}/shell` 被显式禁用。代理会覆盖 `directory` 参数为当前用户工作目录，并按用户工作目录过滤会话列表、项目列表和 SSE 事件。

常用路径包括：
- `GET /opencode/global/event` - SSE 事件流（按工作目录过滤）
- `GET /opencode/session` - 当前用户会话列表
- `POST /opencode/session` - 创建会话并记录归属
- `POST /opencode/session/{id}/prompt_async` - 发送 prompt
- `GET /opencode/session/status` - 当前用户会话状态
- `GET /opencode/project` - 当前工作目录项目列表
- `GET /opencode/path` - 返回当前用户工作目录路径
- `GET /opencode/provider` - Provider 列表

**使用示例（带 JWT）：**

```bash
curl http://localhost:20140/opencode/session \
  -H "Authorization: Bearer your-jwt-token"
```

### 业务 API

| 路径 | 方法 | 认证 | 说明 |
|------|------|------|------|
| `GET /api/business/hello` | GET | JWT | 示例接口 |
| `...` | - | JWT | 在 `routers/business.py` 中扩展 |

## 环境变量

### 前端 (`frontend/.env`)

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `VITE_OPENCODE_PROXY_BASE` | `/opencode` | OpenCode 代理基地址 |
| `VITE_BACKEND_API_URL` | `/api` | 业务 API 基地址 |

开发环境：

```env
VITE_OPENCODE_PROXY_BASE=http://localhost:20140/opencode
VITE_BACKEND_API_URL=http://localhost:20140/api
```

生产环境（同域 Nginx）：

```env
VITE_OPENCODE_PROXY_BASE=/opencode
VITE_BACKEND_API_URL=/api
```

### 后端 (`backend/.env`)

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `JWT_SECRET` | `dev-secret` | JWT 签名密钥（**生产必须修改**） |
| `JWT_ALGORITHM` | `HS256` | JWT 签名算法 |
| `JWT_EXPIRE_DAYS` | `7` | JWT 过期天数 |
| `DATABASE_URL` | `sqlite+aiosqlite:///./dev.db` | SQLAlchemy async 连接串；`.env.example` 使用 MySQL |
| `CORS_ORIGINS` | `http://localhost:4444,...` | CORS 允许的来源 |
| `SANDBOX_<ID>_URL` | - | 沙盒实例 URL |
| `SANDBOX_<ID>_PASSWORD` | - | 沙盒 Basic Auth 密码 |
| `SANDBOX_<ID>_USERNAME` | `opencode` | 沙盒 Basic Auth 用户名 |
| `SANDBOX_WORKSPACE_ROOT` | 自动探测 | 所有沙盒的用户工作目录根路径；生产建议显式设置 |
| `MURTAGH_MCP_URL` | - | 平台内部莫塔医学 MCP 地址，用于按需路由和读取审核后的 skill |
| `DOOR_WINDOW_MCP_URL` | - | 局域网门窗拆图 MCP 服务基地址；配置后 PDF 上传会自动注册图纸 |
| `DOOR_WINDOW_MCP_TOKEN` | - | 门窗拆图 MCP 的 Bearer Token |
| `DOOR_WINDOW_MCP_TIMEOUT_MS` | `120000` | 门窗 MCP 工具调用超时，单位毫秒 |
| `DOOR_WINDOW_MCP_TIMEOUT_SECONDS` | `120` | 平台上传 PDF 注册到门窗 MCP 的超时，单位秒 |
| `SANDBOX_<ID>_WORKSPACE_ROOT` | 全局值 | 指定沙盒的工作目录根路径 |
| `IMAGE_UPLOAD_URL` | - | 平台后端代理上传公众号排版图片的服务地址 |
| `IMAGE_UPLOAD_FIELD_NAME` | `file` | 上游 multipart 图片字段名 |
| `IMAGE_UPLOAD_TIMEOUT_SECONDS` | `120` | 上游图片上传超时，单位秒 |
| `WECHAT_BROKER_BASE_URL` | - | 独立微信授权服务的 HTTPS 基地址 |
| `WECHAT_BROKER_API_KEY` | - | 主项目调用 broker 的服务间密钥 |
| `WECHAT_BROKER_TIMEOUT_SECONDS` | `30` | broker 请求超时秒数 |
| `WECHAT_BROKER_ALLOW_INSECURE_HTTP` | `false` | 仅隔离私网临时允许 HTTP |
| `WECHAT_API_TIMEOUT_SECONDS` | `120` | 当前后端调用微信草稿、素材和发布 API 的超时秒数 |
| `WECHAT_AUTHOR` | - | 微信草稿作者，微信限制内自动截断 |
| `WECHAT_CONTENT_SOURCE_URL` | - | 微信草稿可选原文链接 |
| `WECHAT_NEED_OPEN_COMMENT` | `0` | 是否开启评论，取值 `0` 或 `1` |
| `WECHAT_ONLY_FANS_CAN_COMMENT` | `0` | 是否仅粉丝可评论，取值 `0` 或 `1` |

**多沙盒配置示例：**

```env
SANDBOX_A_URL=http://10.0.0.11:4096
SANDBOX_A_PASSWORD=password-a
SANDBOX_A_USERNAME=opencode

SANDBOX_B_URL=http://10.0.0.12:4096
SANDBOX_B_PASSWORD=password-b
SANDBOX_B_USERNAME=opencode

SANDBOX_C_URL=http://10.0.0.13:4096
SANDBOX_C_PASSWORD=password-c
```

### 助手能力初始化

诊疗、疾病管理和宣教助手都不是独立 OpenCode Agent，而是 `build` 基础 Agent 的能力配置。用户登录后，前端先调用 `POST /api/dispatch`；FastAPI 将所有入口 Skill 去重写入用户工作目录，一次合并全部 MCP 配置，并确认 MCP 已连接。首次工作区初始化才会写配置和刷新实例，后续登录只做幂等检查。创建或切换会话时不再执行安装，前端只通过 `system` 注入对应规则，Agent 按任务加载已安装的 Skill，因此多个会话可以并发运行。

莫塔医学正文不复制到用户目录，而是通过远程 MCP 按需获取。平台新增或升级助手能力时，统一能力包会在下一次 `dispatch` 时更新；配置未变化时不会重载工作区实例。

FastAPI 环境需要配置：

```env
MURTAGH_MCP_URL=http://backend:20140/mcp/murtagh
# Docker Compose 容器内路径
MURTAGH_KNOWLEDGE_ROOT=/app/knowledge/murtagh
```

裸机部署时，`backend/.env` 可以使用服务器绝对路径，也可以使用固定从项目根目录解析的相对路径：

```env
MURTAGH_KNOWLEDGE_ROOT=/www/wwwroot/MedicalAgent/.opencode/skills/disease-guidelines/skill
# 或：MURTAGH_KNOWLEDGE_ROOT=.opencode/skills/disease-guidelines/skill
```

后端始终从 `backend/.env` 加载该变量，不依赖 Uvicorn 的启动目录。修改 `.env` 后需要重启后端进程。

后端内置的 MCP 地址为 `/mcp/murtagh`，提供 `murtagh_route` 和 `murtagh_get_skills` 工具。`MURTAGH_MCP_URL` 必须填写为 OpenCode 沙盒能够访问的内部地址，不能使用 `localhost` 或只在浏览器侧有效的相对路径。远程沙盒开发环境应让 Uvicorn 监听 `0.0.0.0`，填写后端的内网/Tailscale IP，并仅允许沙盒 IP 访问该端口。FastMCP 的 Host 允许列表会自动加入该 URL 中配置的主机和端口；如果实际访问地址与配置不一致，会返回 `421 Invalid Host header`。登录初始化会主动重连并检查统一能力包声明的全部 MCP，全部为 `connected` 后才进入聊天页面。

诊疗入口 Skill 的唯一发布源是 `backend/app/assistant_catalog/clinical-assistant/skills/`。仓库根目录 `.opencode/skills/` 下的同名文件只作为本地 OpenCode 开发镜像，并由测试保证与发布源完全一致。`MURTAGH_KNOWLEDGE_ROOT` 指向 `.opencode/skills/disease-guidelines/skill/`，该目录只保存路由索引、注册表和审核后的医学正文，不是入口 `SKILL.md` 的发布源。生产部署需要把该 `skill` 目录以只读方式挂载，并通过内网、防火墙或反向代理认证限制 MCP 访问。

可在沙盒 OpenCode 上检查 MCP 状态：

```bash
curl -u opencode:你的沙盒密码 \
  "http://沙盒地址:4096/mcp?directory=用户工作目录"
```

正常结果中 `murtagh-medical.status` 应为 `connected`。若为 `failed` 且提示无法连接，优先检查后端监听地址、防火墙、Tailscale ACL 和 `MURTAGH_MCP_URL`。

### 宣教助手能力

“宣教助手”使用登录时已初始化的统一能力包，其中包含 `health-education-content` Skill、远程 `education-content` MCP、`smart-market-hot-topics` MCP 和 `media-generation` MCP。宣教 MCP 只封装独立 `wechat_agent` 的异步生成预览接口；热点 MCP 通过 HTTP 封装独立 Smart Market 后端，不复制其数据库和检索逻辑。

平台开放生成、查看和工作区内可视化编辑微信公众号 HTML，不向 Agent 暴露创建草稿、正式发布或删除接口。平台后端需要配置：

```env
# OpenCode 沙盒能够访问的平台 MCP 地址，不能使用沙盒自身的 localhost。
EDUCATION_MCP_URL=http://backend:20140/mcp/education
EDUCATION_MCP_TIMEOUT_MS=120000
# OpenCode 沙盒访问当前平台热点 MCP 的地址。
HOT_TOPICS_MCP_URL=http://backend:20140/mcp/hot-topics
HOT_TOPICS_MCP_TIMEOUT_MS=60000
# 当前平台后端访问 Smart Market HTTP API 的地址。
SMART_MARKET_BASE_URL=https://mkt.kangqiao.vip/api/v1
SMART_MARKET_TIMEOUT_SECONDS=30
# 平台后端访问独立 wechat_agent 的内网地址。
WECHAT_AGENT_BASE_URL=https://wechat-agent.internal:8330
WECHAT_AGENT_TIMEOUT_SECONDS=30
WECHAT_AGENT_PREVIEW_WAIT_SECONDS=60
WECHAT_AGENT_PREVIEW_POLL_SECONDS=5
# 编辑器替换图片时由平台后端代理调用，不暴露给浏览器。
IMAGE_UPLOAD_URL=https://saas.aitool.backend.kangqiao.vip/api/common/upload
IMAGE_UPLOAD_FIELD_NAME=file
IMAGE_UPLOAD_TIMEOUT_SECONDS=120
# 独立微信授权服务；密钥需与 broker 的 INTERNAL_API_KEY 一致。
WECHAT_BROKER_BASE_URL=https://saas.science.kangqiao.vip/api/wechat-broker
WECHAT_BROKER_API_KEY=replace-with-service-api-key
WECHAT_BROKER_TIMEOUT_SECONDS=30
WECHAT_BROKER_ALLOW_INSECURE_HTTP=false
WECHAT_API_TIMEOUT_SECONDS=120
WECHAT_AUTHOR=
WECHAT_CONTENT_SOURCE_URL=
WECHAT_NEED_OPEN_COMMENT=0
WECHAT_ONLY_FANS_CAN_COMMENT=0
```

宣教 MCP 提供 `education_create_preview`、`education_wait_for_preview`、`education_get_preview` 和 `education_list_templates`。热点 MCP 挂载在 `/mcp/hot-topics`，提供 `hot_topics_search` 和 `hot_topics_list_tracks`，内部调用 `POST /api/v1/track/hot_topics/search` 与 `GET /api/v1/track/tracks`。用户只给出“医院科普宣传”等宽泛意图、受众或图片风格而没有具体医学主题时，Agent 会先询问是否需要结合当前热点素材，不会立即搜索或创建预览；用户确认需要后才搜索。用户已给出明确医学主题且未要求热点时，直接按主题生成。热点只能作为传播背景，不能替代医学证据。

Agent 提交宣教任务并取得 `job_id` 后，使用该编号分段等待后台任务；每段最多 60 秒并明显短于 MCP 超时，未完成时正常返回 `running`，再按同一 `job_id` 续查，不将等待超时视为任务失败。前端按任务编号持续查询，完成后将 `result.html` 幂等保存到 `education-previews/<job_id>.html`，并提供可视化排版编辑、revision 冲突保护、HTML 下载和图片替换。图片替换通过平台后端代理上传，成功地址优先读取 `data.fullurl`，兼容顶层 `fullurl`。热点搜索默认不返回完整正文和图片，以控制模型上下文和上游开销。

微信公众号授权和 token 刷新由仓库中的 `wechat-broker/` 独立服务管理。已有 PHP 继续接收微信开放平台推送并保存最新 `component_verify_ticket`；broker 只读 ticket，并加密保存 component token、authorizer refresh token 和 authorizer access token。当前平台通过内部 HTTPS API 查询绑定或取得短期 token，再由 FastAPI 直接上传正文图片和封面、创建微信草稿并提交正式发布；`wechat_agent` 只负责生成排版预览，不再接收公众号 token。

Broker 返回的授权 URL 会先打开其回调域名下的同域授权发起页，再由该页面跳转微信。前端可以部署在 `medicalagent.kangqiao.vip` 或本地开发地址，而微信检测到的授权发起页和授权回调页仍统一为 Broker 在微信开放平台登记的域名。

Broker 的 MySQL 权限、Nginx 分流、systemd 和虚拟环境部署步骤见 `wechat-broker/README.md`。AppSecret、ticket 数据库账号、Fernet 密钥和 refresh token 不再部署到主项目服务器。

`SMART_MARKET_BASE_URL` 是平台后端访问 Smart Market 的 HTTPS API 基地址，已包含 `/api/v1`，与沙盒访问的 `HOT_TOPICS_MCP_URL` 方向不同。线上必须部署 `POST /track/hot_topics/search` 增强接口；如果后续启用 API Key，可通过 `SMART_MARKET_API_KEY` 注入 `X-API-Key` 请求头。

## 生产部署

### Docker Compose

```bash
# 构建并启动
docker compose up -d

# 查看日志
docker compose logs -f

# 停止
docker compose down
```

### Nginx 配置要点

生产 Nginx 配置（`nginx/default.conf`）包含 SSE 透传的关键设置：

```nginx
location /opencode/ {
    proxy_pass http://backend:20140/opencode/;
    proxy_http_version 1.1;
    proxy_set_header Connection "";

    # SSE 关键配置
    proxy_buffering off;           # 禁用缓冲，事件实时推送
    proxy_cache off;               # 禁用缓存
    proxy_read_timeout 86400s;     # 长连接超时（24小时）
    chunked_transfer_encoding off;
}
```

### 安全清单

| 层 | 措施 |
|----|------|
| 浏览器 → FastAPI | HTTPS + JWT 鉴权 |
| FastAPI → 沙盒 | 内网/Tailscale + Basic Auth（每沙盒独立密码） |
| 沙盒机器 | 防火墙只允许 FastAPI 服务器 IP 访问 4096 端口 |
| OpenCode Server | 必须设置 `OPENCODE_SERVER_PASSWORD` |
| 前端 | 不接触沙盒真实地址和密码 |
| SSE 代理 | 超时 + 心跳检测，断连自动清理 |
| API 响应 | `/api/sandboxes` 不返回沙盒 URL；`/api/sandboxes/{id}/status` 当前会返回 URL，生产建议改为仅管理员可见或移除 URL |

## 沙盒管理

### 手动阶段（当前）

- 每台沙盒机器手动安装 OpenCode 并启动 `opencode serve`
- 在 `backend/.env` 中手动维护沙盒映射
- 用户首次调用 `POST /api/dispatch` 时自动选择当前负载最低的沙盒
- 多个用户可共享同一沙盒，每个用户使用独立随机工作目录
- 用户会话 ID 与所有权记录在平台数据库，直接访问他人会话返回 404
- 沙盒需要提供 Node.js；Windows 还需要 PowerShell，Linux 需要 `/bin/mkdir`、`/bin/rm` 和 `/bin/rmdir`。OpenCode 进程必须有权写入工作目录根路径

> API 层隔离不能替代操作系统权限。若允许 Agent 执行任意 shell，生产环境应为每个用户使用独立容器、独立 Unix 用户或等效的文件系统沙箱，防止进程绕过工作目录读取同机其他目录。

### Docker 阶段（后续）

- FastAPI 通过 Docker API 创建/销毁容器
- 每个容器内运行 `opencode serve`
- 自动生成密码并写入数据库
- 支持按需启动、空闲回收、弹性扩缩容

## 前端与 OpenCode 的关系

### 保留了什么

- OpenCode 原始 UI：会话列表、消息时间线、代码审查、文件浏览、Markdown 渲染
- OpenCode SDK：通过 `@opencode-ai/sdk`（npm 包）调用 OpenCode API
- OpenCode 前端上下文：`server-sdk`、`server-sync`、`server-session` 等

### 改了什么

- **入口地址**：`entry.tsx` 默认指向 FastAPI 代理（`/opencode`），不再直连 `localhost:4096`
- **工具函数**：5 个纯函数从 `@opencode-ai/core/util/*` 内联到 `src/lib/`
- **UI 组件路径**：`@opencode-ai/ui/*` → `@/ui/*`，`@opencode-ai/session-ui/*` → `@/session-ui/*`
- **新增业务 API client**：`src/api/backend.ts` 用于调用 FastAPI 业务接口

### 不依赖什么

- 不依赖 `@opencode-ai/core`（后端包）
- 不依赖 `@opencode-ai/schema` / `@opencode-ai/server` / `@opencode-ai/protocol`
- 不依赖 OpenCode monorepo 的 workspace 机制

## 扩展指南

### 新增业务接口

1. 在 `backend/app/routers/business.py` 添加路由：

```python
@router.get("/tasks")
async def list_tasks(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # 查询业务数据
    ...
```

2. 在 `frontend/src/api/backend.ts` 添加调用：

```ts
export const backend = {
  ...,
  listTasks: () => apiFetch<Task[]>("/business/tasks"),
}
```

3. 在前端页面中使用。

### 新增沙盒

1. 启动新的沙盒机器：`opencode serve --hostname 0.0.0.0 --port 4096`
2. 在 `backend/.env` 添加：

```env
SANDBOX_D_URL=http://10.0.0.14:4096
SANDBOX_D_PASSWORD=new-password
```

3. 重启 FastAPI

### 接入已有 SSO

替换 `backend/app/auth/` 中的 JWT 逻辑为 OAuth/OIDC：

```python
# app/auth/oauth.py
from authlib.integrations.starlette_client import OAuth

oauth = OAuth()
oauth.register("your-sso", ...)
```

## 技术栈

| 层 | 技术 |
|----|------|
| 前端框架 | SolidJS 1.9 |
| 前端构建 | Vite 7 |
| 前端样式 | TailwindCSS 4 |
| 前端状态 | @tanstack/solid-query |
| 后端框架 | FastAPI |
| 后端语言 | Python 3.12+ |
| 后端 ORM | SQLAlchemy 2.0 (async) |
| 后端数据库 | MySQL 8.0（aiomysql 异步驱动） |
| 认证 | JWT (pyjwt) + bcrypt |
| HTTP 代理 | httpx (async) |
| 反向代理 | Nginx |
| 容器化 | Docker / Docker Compose |

## 常见问题

### 前端 npm install 失败

`ghostty-web` 是 GitHub 依赖，需要 git 和网络访问。如果不需要终端功能，可以从 `package.json` 移除并删除相关终端组件。

### SSE 事件流不工作

检查 Nginx 配置是否设置了 `proxy_buffering off`。如果 FastAPI 直接暴露（无 Nginx），检查 httpx timeout 是否足够长。

### 沙盒连接失败

1. 确认沙盒机器上 `opencode serve` 正在运行
2. 确认 `OPENCODE_SERVER_PASSWORD` 已设置
3. 确认 FastAPI 服务器能访问沙盒 IP:端口
4. 检查 `backend/.env` 中的 `SANDBOX_*_URL` 和 `SANDBOX_*_PASSWORD` 是否正确

### 前端类型检查报错

运行 `npm run typecheck`，可能有少量类型错误需要修复（因为从 monorepo 拆分时部分类型定义路径变化）。逐个按报错修复即可。

### CORS 错误

确认 `backend/.env` 中 `CORS_ORIGINS` 包含前端地址（如 `http://localhost:4444`）。

## License

MIT
