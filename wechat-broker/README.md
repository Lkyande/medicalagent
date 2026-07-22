# WeChat Open Platform Broker

该服务部署在 `8.137.189.226`，复用现有 PHP 写入 MySQL 的
`component_verify_ticket`。它集中持有微信 AppSecret、加密后的 refresh token 和
短期 access token；其他项目只通过带 API Key 的 HTTPS API访问。

## 边界

- PHP 仍是 `component_verify_ticket` 的唯一写入方。
- Broker 只读取 `fa_weixin_component_verify_ticket(name, ticket_value)`。
- 微信授权回调由 Broker 接收，现有 PHP 授权事件接收 URL 不变。
- 主项目不保存 AppSecret、ticket、Fernet 密钥或 refresh token。
- 内部 API返回短期 authorizer access token，绝不返回 refresh token。
- 一个 `subjectId` 最多绑定一个公众号，一个公众号最多属于一个 `subjectId`。

## API

公开接口：

```text
GET /api/wechat-broker/authorization/start
GET /api/wechat-broker/authorization/callback
```

内部创建授权接口返回的 `authUrl` 指向 Broker 同域的 `authorization/start` 页面，
该页面再跳转到微信。这样即使主项目前端部署在其他域名，微信看到的授权发起页与
授权回调页仍使用 `WECHAT_BROKER_AUTHORIZATION_CALLBACK_URL` 的域名。真实微信
授权 URL 及其 HMAC 签名只保存在浏览器 fragment 中，不会进入 Nginx 请求日志；
发起页通过同源 POST 交给 Broker 验签后才会跳转微信。

内部接口均要求 `X-API-Key`：

```text
POST   /api/wechat-broker/internal/v1/authorizations
GET    /api/wechat-broker/internal/v1/authorizations/{authorizationId}
GET    /api/wechat-broker/internal/v1/bindings/{subjectId}
POST   /api/wechat-broker/internal/v1/bindings/{subjectId}/access-token
POST   /api/wechat-broker/internal/v1/bindings/{subjectId}/access-token/invalidate
DELETE /api/wechat-broker/internal/v1/bindings/{subjectId}
```

创建授权：

```json
{
  "subjectId": "主项目用户UUID",
  "tenantId": "可选租户ID",
  "returnUrl": "https://主项目域名/wechat/authorization/result"
}
```

`returnUrl` 可省略。使用时其 origin 必须包含在
`WECHAT_BROKER_ALLOWED_RETURN_ORIGINS`；回调会附加
`wechatAuthorizationId` 和 `wechatAuthorizationStatus` 查询参数。

## 1. 部署代码

服务器需要 Python 3.11 或 3.12。以下示例假设仓库位于：

```text
/www/wwwroot/your-platform
```

```bash
cd /www/wwwroot/your-platform/wechat-broker
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

不要将 `.env` 提交到 Git。

## 2. 创建 MySQL 账号

先替换 `deploy/mysql.sql.example` 中的两个随机密码，再由 MySQL 管理员执行对应
SQL。不要直接执行仍包含 `REPLACE_WITH_...` 的模板。

FastAPI 业务账号只管理 `wechat_broker`，ticket reader 只有目标两列的 SELECT
权限。两者都不能使用 PHP 的全权限业务账号。

## 3. 配置环境变量

生成内部 API Key：

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

生成 Fernet 密钥：

```bash
.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

编辑 `.env`：

```env
WECHAT_BROKER_DATABASE_URL=mysql+aiomysql://wechat_broker_app:URL编码后的密码@127.0.0.1:3306/wechat_broker
WECHAT_BROKER_INTERNAL_API_KEY=生成的服务间密钥
WECHAT_BROKER_COMPONENT_APPID=第三方平台AppID
WECHAT_BROKER_COMPONENT_APPSECRET=轮换后的第三方平台AppSecret
WECHAT_BROKER_AUTHORIZATION_CALLBACK_URL=https://saas.science.kangqiao.vip/api/wechat-broker/authorization/callback
WECHAT_BROKER_TOKEN_ENCRYPTION_KEY=生成并永久备份的Fernet密钥
WECHAT_BROKER_REQUIRED_FUNCTION_IDS=
WECHAT_BROKER_ALLOWED_RETURN_ORIGINS=https://主项目域名
WECHAT_BROKER_WECHAT_API_TIMEOUT_SECONDS=30
WECHAT_BROKER_TICKET_DATABASE_URL=mysql+aiomysql://wechat_ticket_reader:URL编码后的密码@127.0.0.1:3306/saasscience
WECHAT_BROKER_TICKET_DATABASE_TABLE=fa_weixin_component_verify_ticket
WECHAT_BROKER_TICKET_DATABASE_SSL=false
WECHAT_BROKER_TICKET_DATABASE_SSL_VERIFY=true
```

数据库密码包含 `@`、`:`、`/`、`#`、`%` 等字符时必须进行 URL 编码：

```bash
python3 -c "from urllib.parse import quote; print(quote('替换为密码', safe=''))"
```

`WECHAT_BROKER_TOKEN_ENCRYPTION_KEY` 更换后，已有 refresh token 将无法解密，必须
让用户重新授权。

## 4. 启动前验证

```bash
.venv/bin/python -c "from app.config import validate_settings; validate_settings(); print('config ok')"
.venv/bin/python -m unittest discover -s tests -v
```

首次启动会创建 broker 自己的表，不修改 PHP ticket 表。

## 5. systemd

确认服务器存在 `www` 用户；如果使用其他服务账号，同时修改 unit 中的
`User`、`Group` 和目录权限。

```bash
cp deploy/wechat-broker.service /etc/systemd/system/wechat-broker.service
systemctl daemon-reload
systemctl enable --now wechat-broker
systemctl status wechat-broker
journalctl -u wechat-broker -n 100 --no-pager
```

服务只监听 `127.0.0.1:18000`，不会与 FreeSWITCH 的 `8000` 冲突。不要配置多个
Uvicorn worker；当前 token 刷新和发布重试依赖单进程锁加数据库行锁。

本机验证：

```bash
curl -i http://127.0.0.1:18000/health
curl -i http://127.0.0.1:18000/ready
curl -i http://127.0.0.1:18000/api/wechat-broker/authorization/callback
```

最后一个接口无参数时应返回 `422`。

## 6. Nginx

将 `deploy/nginx-location.conf` 的三个 location 加入
`saas.science.kangqiao.vip` 对应的 HTTPS server 块。必须先把
内部 API location 中的示例 `allow` 改为主项目服务器的固定出口 IP 并取消注释。
`authorization/start` 和 `authorization/callback` 是公开浏览器入口，不能放入内部
API 的 IP 白名单；两者都关闭访问日志，避免临时授权凭据进入日志。

```bash
nginx -t
systemctl reload nginx
```

验证：

```bash
curl -i https://saas.science.kangqiao.vip/api/wechat-broker/authorization/callback
curl -i https://saas.science.kangqiao.vip/api/wechat-broker/authorization/start
```

callback 无参数时必须返回 `422`，start 必须返回 `200`，而不是 `502`。内部 API从非白名单 IP访问应返回
`403`；从主项目服务器访问但未携带 API Key 应返回 `401`。

## 7. 主项目配置

主项目服务器只配置：

```env
WECHAT_BROKER_BASE_URL=https://saas.science.kangqiao.vip/api/wechat-broker
WECHAT_BROKER_API_KEY=与Broker的INTERNAL_API_KEY完全一致
WECHAT_BROKER_TIMEOUT_SECONDS=30
WECHAT_BROKER_ALLOW_INSECURE_HTTP=false
```

不要把这些值放入前端环境变量。

## 8. 微信平台检查

- 现有 PHP 授权事件接收 URL 保持不变。
- `WECHAT_BROKER_AUTHORIZATION_CALLBACK_URL` 的域名必须与第三方平台填写的授权发起页域名一致。
- 主项目前端可以使用其他域名；浏览器必须先打开 Broker 返回的同域 `authorization/start` 页面，不能直接打开微信 URL。
- 微信 API IP 白名单添加 `8.137.189.226` 上 Broker 的实际出口 IP。
- AppSecret、数据库密码和 Fernet 密钥泄露后必须立即轮换。

部署完成后，从主项目调用创建授权接口，管理员扫码，然后轮询 binding 状态。
