# WeChat Broker 接口文档

本文档描述 WeChat Open Platform Broker `v1.0.0` 的 HTTP 接口，主要供主项目服务端接入。

## 1. 接入信息

### 1.1 服务地址

| 环境 | 地址 |
| --- | --- |
| 生产环境 Base URL | `https://saas.science.kangqiao.vip/api/wechat-broker` |
| 本机 Uvicorn | `http://127.0.0.1:18000` |

下文内部接口路径均相对于生产环境 Base URL。例如：

```text
POST https://saas.science.kangqiao.vip/api/wechat-broker/internal/v1/authorizations
```

健康检查直接位于服务根路径，不使用上述 Base URL：

```text
GET http://127.0.0.1:18000/health
GET http://127.0.0.1:18000/ready
```

### 1.2 数据格式

- 除 HTML 页面和 `204 No Content` 外，请求、响应均使用 JSON。
- JSON 请求应携带 `Content-Type: application/json`。
- 日期时间使用 ISO 8601 格式，通常为 UTC，例如 `2026-08-03T12:34:56.789012Z`。
- JSON 字段使用 camelCase。

### 1.3 内部接口鉴权

所有 `/internal/v1/...` 接口必须由受信任的服务端调用，并携带：

```http
X-API-Key: <WECHAT_BROKER_API_KEY>
```

API Key 必须与 Broker 的 `WECHAT_BROKER_INTERNAL_API_KEY` 完全一致。不要将 API Key 暴露到浏览器、移动端代码或前端环境变量。

生产环境还会通过 Nginx 对内部接口设置来源 IP 白名单。非白名单来源会在到达应用前返回 `403 Forbidden`。

鉴权失败响应：

```http
HTTP/1.1 401 Unauthorized
Content-Type: application/json
```

```json
{
  "detail": "Invalid internal API key"
}
```

若 Broker 未配置内部 API Key，则返回 `503 Service Unavailable`：

```json
{
  "detail": "Internal API authentication is not configured"
}
```

## 2. 推荐接入流程

1. 主项目服务端调用“创建授权”，保存 `authorizationId`，并把 `authUrl` 返回给前端。
2. 前端在浏览器中打开 `authUrl`。该地址必须直接打开，不能提取或改写其中的 fragment。
3. 管理员在微信页面完成公众号授权。
4. 配置了 `returnUrl` 时，Broker 使用 `303 See Other` 跳回该地址，并附加授权结果参数；未配置时返回一个可自动关闭的结果页。
5. 主项目服务端根据 `authorizationId` 查询授权状态，或根据 `subjectId` 查询绑定状态。不要仅依据浏览器传回的查询参数认定授权成功。
6. 调用微信业务接口前，主项目服务端向 Broker 获取 authorizer access token。
7. 若微信拒绝该 token，调用“失效 Access Token”，再重新获取一次并重试原微信请求。

## 3. 通用响应和状态码

### 3.1 错误格式

业务错误通常为：

```json
{
  "detail": "错误说明"
}
```

参数校验失败返回 `422 Unprocessable Entity`，`detail` 为 FastAPI/Pydantic 标准校验错误数组。

### 3.2 通用状态码

| 状态码 | 含义 |
| --- | --- |
| `200 OK` | 请求成功 |
| `204 No Content` | 操作成功且无响应体 |
| `303 See Other` | 微信回调完成后跳转到主项目 `returnUrl` |
| `400 Bad Request` | 授权参数、返回地址或签名无效 |
| `401 Unauthorized` | `X-API-Key` 缺失或错误 |
| `403 Forbidden` | 请求来源不在生产环境 Nginx IP 白名单中 |
| `404 Not Found` | 授权记录不存在 |
| `409 Conflict` | 公众号未授权或授权已失效，需要重新授权 |
| `422 Unprocessable Entity` | 请求体、路径或查询参数校验失败 |
| `500 Internal Server Error` | 未处理的服务端或数据库异常 |
| `502 Bad Gateway` | 微信 API 不可用、返回错误或响应格式无效 |
| `503 Service Unavailable` | Broker 配置、密钥或 ticket 数据源不可用 |

## 4. 内部接口

### 4.1 创建授权

创建一次有效期为 10 分钟的公众号授权尝试。

```http
POST /internal/v1/authorizations
X-API-Key: <API Key>
Content-Type: application/json
```

请求体：

| 字段 | 类型 | 必填 | 限制 | 说明 |
| --- | --- | --- | --- | --- |
| `subjectId` | string | 是 | 1-128 字符 | 主项目中的用户、组织或其他绑定主体 ID |
| `tenantId` | string/null | 否 | 最多 128 字符 | 可选租户 ID |
| `returnUrl` | string/null | 否 | 最多 2048 字符 | 授权完成后的浏览器返回地址 |

`returnUrl` 的 origin 必须在 Broker 的 `WECHAT_BROKER_ALLOWED_RETURN_ORIGINS` 白名单中，且 URL 不得包含用户名或密码。白名单只校验 origin，path、query 和 fragment 可以由主项目按需设置。

请求示例：

```json
{
  "subjectId": "user-7d37d975",
  "tenantId": "tenant-1001",
  "returnUrl": "https://app.example.com/settings/wechat/result?source=broker"
}
```

成功响应：`200 OK`

```json
{
  "authorizationId": "88c7848646b94772a52d8869b88ff772",
  "status": "pending",
  "subjectId": "user-7d37d975",
  "tenantId": "tenant-1001",
  "authorizerAppid": null,
  "authUrl": "https://saas.science.kangqiao.vip/api/wechat-broker/authorization/start#<signed-payload>",
  "expiresAt": "2026-08-03T12:34:56.789012Z"
}
```

响应字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `authorizationId` | string | 授权尝试 ID，后续用于查询结果 |
| `status` | string | 创建时固定为 `pending` |
| `subjectId` | string | 请求中的主体 ID |
| `tenantId` | string/null | 请求中的租户 ID |
| `authorizerAppid` | string/null | 授权完成后得到的公众号 AppID，创建时为 `null` |
| `authUrl` | string | 浏览器应打开的 Broker 授权发起地址 |
| `expiresAt` | datetime | 授权尝试过期时间 |

注意：

- `authUrl` 包含带签名的临时数据，不应记录到业务日志、埋点或第三方分析系统。
- `authUrl` 必须在浏览器中完整打开。fragment 不会进入首次 HTTP 请求日志，页面会在同源验签后跳转微信。
- 重复调用会创建新的授权尝试，不具备请求级幂等性。

常见错误：

| 状态码 | `detail` | 处理建议 |
| --- | --- | --- |
| `400` | `Return URL is invalid` | 检查 `returnUrl` 格式及是否包含用户名或密码 |
| `400` | `Return URL origin is not allowed` | 将 origin 加入 Broker 白名单或省略 `returnUrl` |
| `502` | 微信 API 错误信息 | 稍后重试，并保留服务端错误日志 |
| `503` | Broker 配置或 ticket 错误信息 | 检查 Broker 配置及 ticket 数据源 |

调用示例：

```bash
curl -X POST \
  'https://saas.science.kangqiao.vip/api/wechat-broker/internal/v1/authorizations' \
  -H 'X-API-Key: REPLACE_WITH_API_KEY' \
  -H 'Content-Type: application/json' \
  -d '{"subjectId":"user-7d37d975","tenantId":"tenant-1001","returnUrl":"https://app.example.com/settings/wechat/result"}'
```

### 4.2 查询授权状态

```http
GET /internal/v1/authorizations/{authorization_id}
X-API-Key: <API Key>
```

路径参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `authorization_id` | string | 是 | 创建授权接口返回的 `authorizationId` |

成功响应：`200 OK`

```json
{
  "authorizationId": "88c7848646b94772a52d8869b88ff772",
  "status": "authorized",
  "subjectId": "user-7d37d975",
  "tenantId": "tenant-1001",
  "authorizerAppid": "wx1234567890abcdef",
  "authUrl": null,
  "expiresAt": "2026-08-03T12:34:56.789012Z"
}
```

`status` 可能值：

| 值 | 含义 |
| --- | --- |
| `pending` | 等待管理员完成授权 |
| `processing` | Broker 正在处理微信回调 |
| `authorized` | 授权成功，binding 已生效 |
| `failed` | 授权失败 |
| `expired` | 授权尝试已超过有效期 |

查询接口的 `authUrl` 为 `null`，不会重新返回临时授权地址。需要再次授权时，应重新调用创建授权接口。

授权记录不存在时返回：

```http
HTTP/1.1 404 Not Found
```

```json
{
  "detail": "Authorization not found"
}
```

### 4.3 查询公众号绑定

查询主体当前是否存在有效的公众号绑定。未绑定也是正常查询结果，返回 `200 OK` 而不是 `404 Not Found`。

```http
GET /internal/v1/bindings/{subject_id}
X-API-Key: <API Key>
```

路径参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `subject_id` | string | 是 | 主项目主体 ID |

已授权响应：

```json
{
  "authorized": true,
  "status": "active",
  "subjectId": "user-7d37d975",
  "tenantId": "tenant-1001",
  "authorizerAppid": "wx1234567890abcdef",
  "authorizedAt": "2026-08-03T12:30:00Z"
}
```

未授权响应：

```json
{
  "authorized": false,
  "status": "not_authorized",
  "subjectId": "user-7d37d975",
  "tenantId": null,
  "authorizerAppid": null,
  "authorizedAt": null
}
```

已撤销响应示例：

```json
{
  "authorized": false,
  "status": "revoked",
  "subjectId": "user-7d37d975",
  "tenantId": "tenant-1001",
  "authorizerAppid": null,
  "authorizedAt": null
}
```

只有 `authorized=true` 且 `status=active` 时，主项目才能把该绑定视为可用。

### 4.4 获取 Authorizer Access Token

获取某个主体所绑定公众号的短期 authorizer access token。Broker 会优先返回仍有足够有效期的缓存 token，否则使用加密保存的 refresh token 向微信刷新。

```http
POST /internal/v1/bindings/{subject_id}/access-token
X-API-Key: <API Key>
```

该接口没有请求体。

成功响应：`200 OK`

```json
{
  "accessToken": "ACCESS_TOKEN_FROM_WECHAT",
  "expiresAt": "2026-08-03T14:32:56Z",
  "authorizerAppid": "wx1234567890abcdef"
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `accessToken` | string | 调用微信公众号接口使用的短期 token |
| `expiresAt` | datetime | Broker 侧缓存有效期 |
| `authorizerAppid` | string | token 所属公众号 AppID |

未绑定或绑定已撤销时返回：

```http
HTTP/1.1 409 Conflict
```

```json
{
  "detail": "Official account authorization is required"
}
```

微信确认授权已过期时，Broker 会将本地 binding 标记为 `revoked` 并返回：

```json
{
  "detail": "Official account authorization has expired"
}
```

收到 `409` 后应引导管理员重新授权，不应持续重试获取 token。

安全要求：

- Broker 不会返回 authorizer refresh token。
- 主项目应只在服务端内存或受控短期缓存中使用 `accessToken`。
- 不要将 `accessToken` 返回前端或写入普通应用日志。

### 4.5 失效 Authorizer Access Token

当微信业务接口明确拒绝刚刚使用的 token 时，通知 Broker 条件性清除该 token 缓存。

```http
POST /internal/v1/bindings/{subject_id}/access-token/invalidate
X-API-Key: <API Key>
Content-Type: application/json
```

请求体：

| 字段 | 类型 | 必填 | 限制 | 说明 |
| --- | --- | --- | --- | --- |
| `accessToken` | string | 是 | 1-4096 字符 | 被微信拒绝的 token 原值 |

```json
{
  "accessToken": "REJECTED_ACCESS_TOKEN"
}
```

成功响应：`204 No Content`

此操作具有并发保护：只有请求中的 token 与 Broker 当前缓存 token 相同时才会清除缓存。如果另一个请求已经刷新出新 token，使用旧 token 发起的失效请求不会误删新 token。

binding 不存在、没有缓存 token 或 token 已经更换时也返回 `204`，因此该接口可安全重复调用。

推荐重试流程：

1. 保存本次调用微信所使用的 token。
2. 微信返回 token 无效或过期错误时，把该 token 原值传给失效接口。
3. 再调用一次获取 Access Token 接口。
4. 使用新 token 重试原微信请求一次。
5. 仍失败时停止自动重试并记录微信错误，避免循环请求。

### 4.6 撤销本地绑定

```http
DELETE /internal/v1/bindings/{subject_id}
X-API-Key: <API Key>
```

成功响应：`204 No Content`

行为说明：

- binding 存在时，将本地状态标记为 `revoked` 并清除缓存的 authorizer access token。
- binding 不存在时仍返回 `204`，接口具备幂等性。
- 此操作仅撤销 Broker 本地绑定，不会调用微信接口执行远端取消授权。
- 重新使用该公众号需要创建新的授权并由管理员完成微信授权流程。

## 5. 浏览器公开接口

以下接口用于授权页面和微信回调，不需要 `X-API-Key`，不应由主项目作为普通服务端 API 直接调用。

### 5.1 授权发起页

```http
GET /authorization/start#<signed-payload>
```

完整路径：

```text
GET /api/wechat-broker/authorization/start#<signed-payload>
```

该地址由创建授权接口通过 `authUrl` 返回。页面读取 fragment，通过同源接口验签并解析目标地址，再跳转至：

```text
https://mp.weixin.qq.com/cgi-bin/componentloginpage
```

成功 HTTP 响应为 `200 OK` HTML，并包含禁止缓存、限制引用来源和禁止嵌入 iframe 的安全响应头。payload 缺失、损坏或验签失败时，页面不会跳转微信，而是显示授权地址无效提示。

### 5.2 解析授权目标

```http
POST /authorization/start/resolve
Content-Type: application/json
```

完整路径：

```text
POST /api/wechat-broker/authorization/start/resolve
```

该接口仅供授权发起页同源调用。

请求体：

```json
{
  "payload": "<base64url-data>.<hmac-sha256-hex>"
}
```

`payload` 必填，长度为 1-8192 字符。

成功响应：`200 OK`

```json
{
  "target": "https://mp.weixin.qq.com/cgi-bin/componentloginpage?..."
}
```

签名、编码或目标地址不合法时返回 `400 Bad Request`：

```json
{
  "detail": "Authorization start payload is invalid"
}
```

### 5.3 微信授权回调

```http
GET /authorization/callback?state=<state>&auth_code=<auth_code>
```

完整路径：

```text
GET /api/wechat-broker/authorization/callback
```

查询参数：

| 参数 | 类型 | 必填 | 限制 | 说明 |
| --- | --- | --- | --- | --- |
| `state` | string | 是 | 恰好 64 字符 | Broker 创建授权时生成的一次性状态值 |
| `auth_code` | string | 是 | 1-512 字符 | 微信返回的授权码 |

该接口由微信浏览器流程调用。state 有效期为 10 分钟，且只能成功使用一次；重复或并发回调不会重复完成授权。

配置了 `returnUrl` 时，成功后返回：

```http
HTTP/1.1 303 See Other
Location: https://app.example.com/settings/wechat/result?wechatAuthorizationId=88c7848646b94772a52d8869b88ff772&wechatAuthorizationStatus=authorized
```

可附加的结果参数：

| 参数 | 可能值 | 说明 |
| --- | --- | --- |
| `wechatAuthorizationId` | 授权尝试 ID | 用于服务端查询最终状态 |
| `wechatAuthorizationStatus` | `authorized` / `failed` | 浏览器流程的结果提示 |

如果 `returnUrl` 已包含同名参数，Broker 会覆盖它们。原 URL 的 fragment 会保留。

未配置 `returnUrl` 时，接口返回 `200 OK` HTML 结果页，并在 800ms 后尝试关闭当前窗口。

重要说明：

- 大多数回调业务失败返回 `303` 失败跳转或 `200` HTML 错误页，不一定返回 HTTP 4xx/5xx。
- 无效、过期或已使用的 state 无法关联到原授权记录时，只能返回 `200` HTML 错误页。
- 主项目必须通过内部查询接口确认 `authorized` 或有效 binding，不能把回调查询参数作为唯一可信依据。
- 缺少查询参数或参数长度不合法时返回 `422 Unprocessable Entity`。

## 6. 健康检查

### 6.1 存活检查

```http
GET /health
```

成功响应：`200 OK`

```json
{
  "status": "ok"
}
```

该接口只表示应用进程可处理请求，不检查数据库、ticket 数据源或微信 API。

### 6.2 就绪检查

```http
GET /ready
```

成功响应：`200 OK`

```json
{
  "status": "ready"
}
```

该接口会执行 Broker 数据库连接检查，但不检查 ticket 数据源和微信 API。数据库不可用时通常返回 `500 Internal Server Error`。

## 7. 主项目实现建议

### 7.1 授权状态轮询

- 浏览器打开 `authUrl` 后，每 2-3 秒查询一次授权状态。
- 状态为 `authorized`、`failed` 或 `expired` 时停止轮询。
- 状态为 `processing` 时继续短暂轮询，不要立即创建新的授权。
- 达到 `expiresAt` 后停止轮询，并允许用户重新发起授权。
- 浏览器从 `returnUrl` 返回时，可以立即触发一次查询，但仍以服务端查询结果为准。

### 7.2 超时和重试

- 主项目调用 Broker 建议设置 30 秒超时。
- `GET` 查询接口可对网络错误和 `502` 做有限次数退避重试。
- 创建授权不是幂等操作，网络超时后不要无上限自动重试，否则会产生多个有效授权尝试。
- `204` 的 token 失效和撤销绑定接口可安全重复调用。
- `409` 表示需要用户重新授权，不应按临时故障持续重试。

### 7.3 安全边界

- 所有内部接口只能由主项目服务端调用。
- API Key、access token、`authUrl`、微信响应原文不得写入公开日志。
- 主项目不应保存 AppSecret、component ticket、Fernet 密钥或 authorizer refresh token。
- 一个 `subjectId` 最多绑定一个公众号，一个公众号最多属于一个 `subjectId`。
- 生产调用必须使用 HTTPS；不要绕过 Nginx IP 白名单直接暴露 Uvicorn 端口。

## 8. OpenAPI

FastAPI 默认提供以下自动文档地址。本机调试时可访问：

```text
http://127.0.0.1:18000/docs
http://127.0.0.1:18000/redoc
http://127.0.0.1:18000/openapi.json
```

生产环境是否对外开放这些路径取决于 Nginx 配置。本文档中的业务流程、安全约束和回调语义比自动生成的模型文档更完整。
