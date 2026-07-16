# Your Platform

基于 [OpenCode](https://opencode.ai) 的 AI 编码平台，保留原始 Agent 功能，新增自定义业务逻辑。

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
2. 前端调用 `POST /api/dispatch` → FastAPI 选择负载最低的沙盒并创建随机用户工作目录
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
│   │   ├── models.py          # User / SandboxAssignment 模型
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
pip install -r requirements.txt

# 配置环境
cp .env.example .env
# 编辑 .env，填入沙盒 IP 和密码
#   SANDBOX_A_URL=http://10.0.0.11:4096
#   SANDBOX_A_PASSWORD=你的沙盒密码
#   JWT_SECRET=你的JWT密钥

# 启动开发服务器
uvicorn app.main:app --reload --port 8000
```

### 3. 启动前端

```bash
cd frontend

# 安装依赖
npm install

# 配置环境（开发环境）
# .env 已预配置：
#   VITE_OPENCODE_PROXY_BASE=http://localhost:8000/opencode
#   VITE_BACKEND_API_URL=http://localhost:8000/api

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

### 认证

| 路径 | 方法 | 认证 | 说明 |
|------|------|------|------|
| `POST /api/auth/register` | POST | 无 | 用户注册，返回 JWT |
| `POST /api/auth/login` | POST | 无 | 用户登录，返回 JWT |
| `GET /api/auth/me` | GET | JWT | 当前用户信息 |

**注册示例：**

```bash
curl -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"your-password"}'
```

**响应：**

```json
{
  "token": "eyJ...",
  "user": { "id": "...", "email": "user@example.com", "sandbox_id": null }
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

所有 OpenCode HTTP API 都通过此路径转发，包括：
- `GET /opencode/global/event` — SSE 事件流（透传）
- `GET /opencode/session` — 会话列表
- `POST /opencode/session/{id}/prompt_async` — 发送 prompt
- `GET /opencode/project` — 项目列表
- `GET /opencode/config` — 配置
- `GET /opencode/provider` — Provider 列表
- ... 其他所有 OpenCode API

**使用示例（带 JWT）：**

```bash
curl http://localhost:8000/opencode/session \
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
VITE_OPENCODE_PROXY_BASE=http://localhost:8000/opencode
VITE_BACKEND_API_URL=http://localhost:8000/api
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
| `DATABASE_URL` | `mysql+aiomysql://root:123456@localhost:3320/your_platform` | MySQL 连接串 |
| `CORS_ORIGINS` | `http://localhost:4444,...` | CORS 允许的来源 |
| `SANDBOX_<ID>_URL` | - | 沙盒实例 URL |
| `SANDBOX_<ID>_PASSWORD` | - | 沙盒 Basic Auth 密码 |
| `SANDBOX_<ID>_USERNAME` | `opencode` | 沙盒 Basic Auth 用户名 |
| `SANDBOX_WORKSPACE_ROOT` | 自动探测 | 所有沙盒的用户工作目录根路径；生产建议显式设置 |
| `SANDBOX_<ID>_WORKSPACE_ROOT` | 全局值 | 指定沙盒的工作目录根路径 |

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
    proxy_pass http://backend:8000/opencode/;
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
| API 响应 | `/api/sandboxes` 不返回沙盒 URL |

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
