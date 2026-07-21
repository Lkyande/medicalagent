from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.config import settings
from app.services.smart_market_client import SmartMarketClient


def _hot_topics_transport_security() -> TransportSecuritySettings:
    allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    configured_host = urlparse(settings.hot_topics_mcp_url).netloc
    if configured_host and configured_host not in allowed_hosts:
        allowed_hosts.append(configured_host)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
    )


hot_topics_mcp = FastMCP(
    "smart-market-hot-topics",
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    transport_security=_hot_topics_transport_security(),
)


@hot_topics_mcp.tool()
async def hot_topics_list_tracks() -> dict[str, str]:
    """列出 Smart Market 热点搜索支持的赛道 ID 和中文名称。"""
    return await SmartMarketClient().list_tracks()


@hot_topics_mcp.tool()
async def hot_topics_search(
    keywords: list[str] | None = None,
    keyword_mode: Literal["any", "all", "phrase"] = "any",
    track: str | None = None,
    sources: list[str] | None = None,
    exclude_sources: list[str] | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    relative_days: int | None = 7,
    limit: int = 20,
    cursor: str | None = None,
    sort: Literal["newest", "relevance"] = "relevance",
    balance_sources: bool = True,
    allow_fallback: bool = False,
    include_content: bool = False,
    include_images: bool = False,
) -> dict[str, Any]:
    """按用户要求搜索热点，支持关键词、赛道、来源、时间、分页和结果补充。

    自定义 start_time 和 end_time 时必须同时提供，并将 relative_days 设为 null。
    exclude_sources 是硬约束。只有用户允许扩大时间范围时才设置 allow_fallback。
    """
    normalized_keywords = _normalize_values("keywords", keywords or [], 10, 64)
    normalized_sources = _normalize_values("sources", sources or [], 16, 50, lower=True)
    normalized_excluded = _normalize_values(
        "exclude_sources", exclude_sources or [], 16, 50, lower=True
    )
    if set(normalized_sources) & set(normalized_excluded):
        raise ValueError("sources and exclude_sources must not overlap")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if relative_days is not None and not 1 <= relative_days <= 30:
        raise ValueError("relative_days must be between 1 and 30")
    if relative_days is not None and (start_time is not None or end_time is not None):
        raise ValueError("relative_days cannot be combined with start_time or end_time")
    if (start_time is None) != (end_time is None):
        raise ValueError("start_time and end_time must be provided together")
    if start_time and end_time and start_time > end_time:
        raise ValueError("start_time must not be later than end_time")

    payload = {
        "keywords": normalized_keywords,
        "keyword_mode": keyword_mode,
        "track": track.strip().lower() if track else None,
        "sources": normalized_sources,
        "exclude_sources": normalized_excluded,
        "start_time": start_time.isoformat() if start_time else None,
        "end_time": end_time.isoformat() if end_time else None,
        "relative_days": relative_days,
        "limit": limit,
        "cursor": cursor.strip() if cursor else None,
        "sort": sort,
        "balance_sources": balance_sources,
        "allow_fallback": allow_fallback,
        "include_content": include_content,
        "include_images": include_images,
    }
    result = await SmartMarketClient().search_hot_topics(payload)
    return _compact_search_result(result, include_content=include_content)


def _normalize_values(
    name: str,
    values: list[str],
    max_items: int,
    max_length: int,
    *,
    lower: bool = False,
) -> list[str]:
    if len(values) > max_items:
        raise ValueError(f"{name} supports at most {max_items} values")
    result = []
    for value in values:
        clean_value = value.strip()
        if not clean_value:
            continue
        if len(clean_value) > max_length:
            raise ValueError(f"each {name} value must contain at most {max_length} characters")
        normalized = clean_value.lower() if lower else clean_value
        if normalized not in result:
            result.append(normalized)
    return result


def _compact_search_result(result: dict[str, Any], *, include_content: bool) -> dict[str, Any]:
    items = []
    for item in result.get("items", []):
        if not isinstance(item, dict):
            continue
        compact_item = dict(item)
        if not include_content:
            compact_item.pop("content", None)
        items.append(compact_item)
    return {**result, "items": items}
