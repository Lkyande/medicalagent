from typing import BinaryIO
from urllib.parse import urlparse

import httpx

from app.config import settings


class ImageUploadClient:
    def __init__(self) -> None:
        if not settings.image_upload_url:
            raise RuntimeError("IMAGE_UPLOAD_URL is not configured")
        if not settings.image_upload_field_name.strip():
            raise RuntimeError("IMAGE_UPLOAD_FIELD_NAME is not configured")
        self.url = settings.image_upload_url
        self.field_name = settings.image_upload_field_name.strip()

    async def upload(self, filename: str, source: BinaryIO, mime: str) -> str:
        source.seek(0)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(settings.image_upload_timeout_seconds)) as client:
                response = await client.post(
                    self.url,
                    files={self.field_name: (filename, source, mime)},
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"Image upload returned HTTP {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"Image upload service is unavailable: {exc}") from exc
        except ValueError as exc:
            raise RuntimeError("Image upload returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Image upload returned an invalid response")
        code = payload.get("code")
        valid_code = code == "1" or (isinstance(code, int) and not isinstance(code, bool) and code == 1)
        if code is not None and not valid_code:
            raise RuntimeError("Image upload service rejected the file")
        data = payload.get("data")
        full_url = data.get("fullurl") if isinstance(data, dict) else None
        if not isinstance(full_url, str) or not full_url.strip():
            full_url = payload.get("fullurl")
        if not isinstance(full_url, str):
            raise RuntimeError("Image upload response did not include fullurl")
        full_url = full_url.strip()
        parsed = urlparse(full_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise RuntimeError("Image upload returned an invalid HTTPS URL")
        return full_url
