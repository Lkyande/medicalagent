from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.config import settings
from app.services.murtagh_knowledge import get_skills, route_skills


def _mcp_transport_security() -> TransportSecuritySettings:
    allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    configured_host = urlparse(settings.murtagh_mcp_url).netloc
    if configured_host and configured_host not in allowed_hosts:
        allowed_hosts.append(configured_host)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
    )


murtagh_mcp = FastMCP(
    "murtagh-medical",
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    transport_security=_mcp_transport_security(),
)


@murtagh_mcp.tool()
def murtagh_route(
    query: str,
    diseases: list[str] | None = None,
    symptoms: list[str] | None = None,
    keywords: list[str] | None = None,
    limit: int = 5,
) -> dict:
    """根据去标识后的临床问题路由到候选莫塔医学技能，不代表诊断成立。"""
    return route_skills(query, diseases, symptoms, keywords, limit)


@murtagh_mcp.tool()
def murtagh_get_skills(skill_ids: list[str]) -> dict:
    """按一至五个已路由的 skill_id 获取完整且已审核的莫塔医学技能内容。"""
    return get_skills(skill_ids)
