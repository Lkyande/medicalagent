from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from app.config import settings


_API_BASE = "https://api.weixin.qq.com/cgi-bin"
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_REDIRECTS = 3
_IMAGE_CONTENT_TYPES = {
    "image/gif": "image.gif",
    "image/jpeg": "image.jpg",
    "image/png": "image.png",
    "image/webp": "image.webp",
}
_WECHAT_IMAGE_HOSTS = ("mmbiz.qpic.cn", "mmbiz.qlogo.cn", "mmbiz.qpic.com")
_EDITOR_135_HOSTS = ("135editor.com", "135editor.net")
_FORBIDDEN_TAGS = {"head", "script", "style", "iframe", "form", "link", "meta"}
_QR_CODE_RE = re.compile(r"二维码|扫码|扫描二维码|长按识别|关注公众号|公众号二维码|扫码关注|关注我们", re.I)
_CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I)
_CONTENT_IMAGE_STYLE = "width:100%;height:auto;display:block;border-radius:12px;"
_DOWNLOAD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; WeChatPublisher/1.0)",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}


class WechatPublishUncertainError(RuntimeError):
    """A publish write may have succeeded, but no trustworthy response arrived."""


class _WechatResponseError(RuntimeError):
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        super().__init__(str(payload.get("errmsg") or "WeChat API rejected the request"))


@dataclass(frozen=True)
class _ImageAsset:
    filename: str
    content_type: str
    content: bytes


class WechatPublishService:
    async def sync_official_account_draft(
        self,
        *,
        authorizer_access_token: str,
        html: str,
    ) -> dict:
        article = _parse_article(html)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(settings.wechat_api_timeout_seconds)) as client:
                content, cover = await self._prepare_content(client, authorizer_access_token, article.content)
                thumb_media_id = await self._upload_image(
                    client,
                    authorizer_access_token,
                    "/material/add_material",
                    cover,
                    params={"type": "image"},
                    response_key="media_id",
                )
                payload = await self._post_json(
                    client,
                    "/draft/add",
                    authorizer_access_token,
                    {
                        "articles": [
                            {
                                "title": article.title[:64],
                                "author": settings.wechat_author[:8],
                                "digest": article.digest[:120],
                                "content": content,
                                "content_source_url": settings.wechat_content_source_url,
                                "thumb_media_id": thumb_media_id,
                                "need_open_comment": settings.wechat_need_open_comment,
                                "only_fans_can_comment": settings.wechat_only_fans_can_comment,
                            }
                        ]
                    },
                )
        except _WechatResponseError as exc:
            return exc.payload
        except httpx.RequestError as exc:
            raise RuntimeError("WeChat API is unavailable") from exc
        except (ValueError, TypeError) as exc:
            raise RuntimeError(str(exc)) from exc
        media_id = payload.get("media_id")
        if not isinstance(media_id, str) or not media_id:
            raise RuntimeError("WeChat draft response omitted media_id")
        return payload

    async def submit_official_account_publish(
        self,
        *,
        media_id: str,
        authorizer_access_token: str,
    ) -> dict:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(settings.wechat_api_timeout_seconds)) as client:
                payload = await self._post_json(
                    client,
                    "/freepublish/submit",
                    authorizer_access_token,
                    {"media_id": media_id},
                    allow_wechat_error=True,
                    uncertain=True,
                )
                if not _errcode(payload):
                    for key in ("publish_id", "msg_data_id"):
                        if payload.get(key) is not None:
                            payload[key] = str(payload[key])
                return payload
        except httpx.RequestError as exc:
            raise WechatPublishUncertainError(
                "WeChat publish response was not received"
            ) from exc
        except (ValueError, TypeError) as exc:
            raise WechatPublishUncertainError(
                "WeChat publish returned an invalid response"
            ) from exc

    async def _prepare_content(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        html: str,
    ) -> tuple[str, _ImageAsset]:
        soup = BeautifulSoup(html, "html.parser")
        images = list(soup.find_all("img"))
        cover_src = next(
            (
                str(image.get("src", "")).strip()
                for image in images
                if str(image.get("src", "")).strip()
                and not str(image.get("src", "")).strip().startswith(("data:", "#"))
                and not _is_135editor_image_url(str(image.get("src", "")).strip())
            ),
            "",
        )
        if not cover_src:
            raise RuntimeError("Article has no image; a cover image is required")

        assets: dict[str, _ImageAsset] = {}
        uploaded: dict[str, str] = {}
        cover = await self._download_image(client, cover_src)
        assets[cover_src] = cover

        for image in images:
            src = str(image.get("src", "")).strip()
            if not src:
                image.decompose()
                continue
            try:
                image["src"] = await self._article_image_url(
                    client, access_token, src, assets, uploaded
                )
            except RuntimeError:
                if not _is_135editor_image_url(src):
                    raise
                image.decompose()

        for tag in soup.find_all(style=True):
            style = str(tag.get("style", ""))
            if "url(" not in style.lower():
                continue
            tag["style"] = await self._replace_style_urls(
                client, access_token, style, assets, uploaded
            )

        _normalize_images(soup)
        _normalize_paragraphs(soup)
        return str(soup).strip(), cover

    async def _replace_style_urls(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        style: str,
        assets: dict[str, _ImageAsset],
        uploaded: dict[str, str],
    ) -> str:
        result: list[str] = []
        cursor = 0
        for match in _CSS_URL_RE.finditer(style):
            result.append(style[cursor : match.start()])
            quote = match.group(1)
            src = match.group(2).strip()
            if not src or src.startswith(("data:", "#")):
                result.append(match.group(0))
            else:
                try:
                    uploaded_url = await self._article_image_url(
                        client, access_token, src, assets, uploaded
                    )
                except RuntimeError:
                    if not _is_135editor_image_url(src):
                        raise
                    result.append("none")
                else:
                    result.append(f"url({quote}{uploaded_url}{quote})")
            cursor = match.end()
        result.append(style[cursor:])
        return "".join(result)

    async def _article_image_url(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        src: str,
        assets: dict[str, _ImageAsset],
        uploaded: dict[str, str],
    ) -> str:
        if _is_wechat_image_url(src):
            return src
        if src in uploaded:
            return uploaded[src]
        asset = assets.get(src)
        if asset is None:
            asset = await self._download_image(client, src)
            assets[src] = asset
        uploaded[src] = await self._upload_image(
            client,
            access_token,
            "/media/uploadimg",
            asset,
            response_key="url",
        )
        return uploaded[src]

    async def _download_image(self, client: httpx.AsyncClient, url: str) -> _ImageAsset:
        current = url
        for redirect_count in range(_MAX_REDIRECTS + 1):
            await _validate_public_url(current)
            try:
                response = await client.get(current, headers=_DOWNLOAD_HEADERS, follow_redirects=False)
            except httpx.RequestError as exc:
                raise RuntimeError("Unable to download an article image") from exc
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location or redirect_count == _MAX_REDIRECTS:
                    raise RuntimeError("Article image redirected too many times")
                current = urljoin(current, location)
                continue
            if response.status_code >= 400:
                raise RuntimeError(f"Article image returned HTTP {response.status_code}")
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            filename = _IMAGE_CONTENT_TYPES.get(content_type)
            if filename is None:
                raise RuntimeError("Article image has an unsupported content type")
            content_length = response.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > _MAX_IMAGE_BYTES:
                        raise RuntimeError("Article image exceeds 10 MiB")
                except ValueError:
                    pass
            content = response.content
            if not content:
                raise RuntimeError("Article image is empty")
            if len(content) > _MAX_IMAGE_BYTES:
                raise RuntimeError("Article image exceeds 10 MiB")
            if not _has_valid_image_signature(content_type, content):
                raise RuntimeError("Article image content does not match its content type")
            return _ImageAsset(filename=filename, content_type=content_type, content=content)
        raise RuntimeError("Unable to download an article image")

    async def _upload_image(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        path: str,
        asset: _ImageAsset,
        *,
        response_key: str,
        params: dict[str, str] | None = None,
    ) -> str:
        payload = await self._request_json(
            client,
            "POST",
            path,
            access_token,
            params=params,
            files={"media": (asset.filename, asset.content, asset.content_type)},
        )
        value = payload.get(response_key)
        if not isinstance(value, str) or not value:
            raise RuntimeError(f"WeChat image response omitted {response_key}")
        return value

    async def _post_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        access_token: str,
        body: dict,
        *,
        allow_wechat_error: bool = False,
        uncertain: bool = False,
    ) -> dict:
        try:
            return await self._request_json(
                client,
                "POST",
                path,
                access_token,
                json=body,
                allow_wechat_error=allow_wechat_error,
            )
        except httpx.RequestError:
            if uncertain:
                raise
            raise RuntimeError("WeChat API is unavailable") from None

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        access_token: str,
        *,
        params: dict[str, str] | None = None,
        allow_wechat_error: bool = False,
        **kwargs,
    ) -> dict:
        query = {"access_token": access_token, **(params or {})}
        try:
            response = await client.request(
                method,
                f"{_API_BASE}{path}",
                params=query,
                **kwargs,
            )
        except httpx.RequestError:
            raise
        if response.status_code >= 400:
            raise RuntimeError(f"WeChat API returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ValueError("WeChat API returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise TypeError("WeChat API returned an invalid response")
        errcode = _errcode(payload)
        if errcode and not allow_wechat_error:
            raise _WechatResponseError(payload)
        return payload


@dataclass(frozen=True)
class _Article:
    title: str
    digest: str
    content: str


def _parse_article(html: str) -> _Article:
    soup = BeautifulSoup(_strip_code_fence(html), "html.parser")
    title_parts = [
        tag.get_text(" ", strip=True)
        for tag in soup.select("[data-role='title']")
        if tag.get_text(" ", strip=True)
    ]
    first_heading = soup.find("h1")
    title = " ".join(title_parts) or (
        first_heading.get_text(" ", strip=True) if first_heading else ""
    )
    if not title:
        title = "未命名文章"
    digest_tag = soup.select_one("[data-role='digest']") or soup.find("blockquote")
    digest = digest_tag.get_text(" ", strip=True) if digest_tag else ""
    if not digest:
        digest = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()[:120]
    digest = digest or "文章摘要"

    for tag in list(soup.find_all(_FORBIDDEN_TAGS)):
        tag.decompose()
    for wrapper in list(soup.find_all(["html", "body"])):
        wrapper.unwrap()
    _remove_qr_code_content(soup)
    return _Article(title=title, digest=digest, content=str(soup).strip())


def _remove_qr_code_content(soup: BeautifulSoup) -> None:
    for text_node in list(
        soup.find_all(string=lambda value: isinstance(value, str) and _QR_CODE_RE.search(value))
    ):
        parent = text_node.parent
        if not isinstance(parent, Tag):
            continue
        container = parent.find_parent("section") or parent
        text = container.get_text(" ", strip=True)
        if len(text) <= 120 and len(container.find_all("img")) <= 3:
            container.decompose()


def _normalize_images(soup: BeautifulSoup) -> None:
    for image in list(soup.find_all("img")):
        src = str(image.get("src", "")).strip()
        if not src:
            image.decompose()
            continue
        parent = image.parent
        if isinstance(parent, Tag):
            parent_style = str(parent.get("style", ""))
            parent["style"] = _remove_css_properties(
                parent_style,
                {"padding-top", "padding-bottom", "position", "overflow", "height", "min-height", "max-height"},
            )
            if not parent["style"]:
                parent.attrs.pop("style", None)
        image.attrs = {"src": src, "alt": "文章配图", "style": _CONTENT_IMAGE_STYLE}


def _normalize_paragraphs(soup: BeautifulSoup) -> None:
    for paragraph in soup.find_all("p"):
        text = paragraph.get_text(" ", strip=True)
        if not text or len(text) <= 18 or paragraph.find("img"):
            continue
        if any(
            isinstance(tag, Tag) and tag.get("data-role") in {"title", "digest"}
            for tag in [paragraph, *paragraph.parents]
        ):
            continue
        combined_style = ";".join(
            str(tag.get("style", ""))
            for tag in [paragraph, *paragraph.parents]
            if isinstance(tag, Tag)
        ).replace(" ", "").lower()
        if any(
            marker in combined_style
            for marker in (
                "text-align:center",
                "text-align:right",
                "font-weight:bold",
                "font-size:24",
                "font-size:30",
            )
        ):
            continue
        if text.upper() == "END" or text.startswith(("文字 |", "图片 |", "作者：", "关注")):
            continue
        paragraph["style"] = _merge_css_properties(
            str(paragraph.get("style", "")),
            {"text-indent": "2em", "text-align": "justify"},
        )


def _remove_css_properties(style: str, names: set[str]) -> str:
    declarations: list[str] = []
    for declaration in style.split(";"):
        if ":" not in declaration:
            continue
        name, value = declaration.split(":", 1)
        if name.strip().lower() in names:
            continue
        declarations.append(f"{name.strip()}:{value.strip()}")
    return ";".join(declarations) + (";" if declarations else "")


def _merge_css_properties(style: str, properties: dict[str, str]) -> str:
    declarations: list[tuple[str, str]] = []
    existing: set[str] = set()
    for declaration in style.split(";"):
        if ":" not in declaration:
            continue
        name, value = declaration.split(":", 1)
        normalized = name.strip().lower()
        existing.add(normalized)
        declarations.append((name.strip(), value.strip()))
    for name, value in properties.items():
        if name not in existing:
            declarations.append((name, value))
    return ";".join(f"{name}:{value}" for name, value in declarations) + (
        ";" if declarations else ""
    )


def _strip_code_fence(content: str) -> str:
    text = content.strip()
    lines = text.splitlines()
    if not lines or not lines[0].strip().startswith("```"):
        return text
    lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _is_wechat_image_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in _WECHAT_IMAGE_HOSTS)


def _is_135editor_image_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in _EDITOR_135_HOSTS)


async def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise RuntimeError("Article image must use a public HTTP or HTTPS URL")
    if _is_trusted_wechat_agent_static_url(parsed):
        return
    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo,
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise RuntimeError("Article image host could not be resolved") from exc
    if not addresses:
        raise RuntimeError("Article image host could not be resolved")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise RuntimeError("Article image URL points to a non-public address")


def _is_trusted_wechat_agent_static_url(parsed) -> bool:
    configured = urlparse(settings.wechat_agent_base_url)
    return bool(
        configured.scheme in {"http", "https"}
        and parsed.scheme == configured.scheme
        and parsed.hostname == configured.hostname
        and parsed.port == configured.port
        and parsed.path.startswith("/static/")
    )


def _has_valid_image_signature(content_type: str, content: bytes) -> bool:
    if content_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/gif":
        return content.startswith((b"GIF87a", b"GIF89a"))
    if content_type == "image/webp":
        return len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP"
    return False


def _errcode(payload: dict) -> int:
    try:
        return int(payload.get("errcode") or 0)
    except (TypeError, ValueError) as exc:
        raise TypeError("WeChat API returned an invalid errcode") from exc
