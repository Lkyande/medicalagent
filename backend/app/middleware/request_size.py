import json


class RequestBodyTooLarge(Exception):
    pass


class UploadSizeLimitMiddleware:
    def __init__(self, app, maximum_bytes: int):
        self.app = app
        self.maximum_bytes = maximum_bytes

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope.get("type") != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        if path == "/api/workspace/uploads":
            maximum_bytes = self.maximum_bytes
        elif (path.startswith("/api/education/previews/") and path.endswith("/images")) or path == "/api/education/files/images":
            maximum_bytes = min(self.maximum_bytes, 11 * 1024 * 1024)
        else:
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                if int(content_length) > maximum_bytes:
                    await self._reject(send, maximum_bytes)
                    return
            except ValueError:
                await self._reject(send, maximum_bytes)
                return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > maximum_bytes:
                    raise RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLarge:
            await self._reject(send, maximum_bytes)

    @staticmethod
    async def _reject(send, maximum_bytes: int):
        limit_mib = maximum_bytes // (1024 * 1024)
        body = json.dumps({"detail": f"Upload request exceeds {limit_mib} MiB"}).encode()
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})
