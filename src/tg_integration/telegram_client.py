from __future__ import annotations

from typing import Any


class TelegramAPIError(RuntimeError):
    def __init__(self, method: str, status_code: int, body: Any) -> None:
        super().__init__(f"Telegram {method} failed with HTTP {status_code}: {body}")
        self.method = method
        self.status_code = status_code
        self.body = body


class TelegramClient:
    def __init__(self, *, token: str, base_url: str = "https://api.telegram.org", timeout: float = 10.0) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def request(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        import httpx

        url = f"{self.base_url}/bot{self.token}/{method}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, json=payload)
        try:
            body = response.json()
        except ValueError:
            body = response.text
        if response.is_error or not (isinstance(body, dict) and body.get("ok")):
            raise TelegramAPIError(method, response.status_code, body)
        return body

    async def set_webhook(
        self,
        *,
        url: str,
        secret_token: str,
        drop_pending_updates: bool = False,
    ) -> dict[str, Any]:
        return await self.request(
            "setWebhook",
            {
                "url": url,
                "secret_token": secret_token,
                "allowed_updates": ["message", "edited_message"],
                "drop_pending_updates": drop_pending_updates,
            },
        )

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> dict[str, Any]:
        return await self.request(
            "deleteWebhook",
            {"drop_pending_updates": drop_pending_updates},
        )

    async def get_updates(
        self,
        *,
        offset: int | None = None,
        limit: int = 50,
        timeout: int = 25,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "limit": limit,
            "timeout": timeout,
            "allowed_updates": ["message", "edited_message"],
        }
        if offset is not None:
            payload["offset"] = offset
        response = await self.request("getUpdates", payload)
        result = response.get("result") if isinstance(response, dict) else None
        return result if isinstance(result, list) else []

    async def send_text(self, *, chat_id: str, text: str) -> dict[str, Any]:
        return await self.request("sendMessage", {"chat_id": chat_id, "text": text})

    async def get_file_url(self, *, file_id: str) -> str:
        response = await self.request("getFile", {"file_id": file_id})
        result = response.get("result") if isinstance(response, dict) else {}
        file_path = result.get("file_path") if isinstance(result, dict) else None
        if not file_path:
            raise TelegramAPIError("getFile", 200, response)
        return f"{self.base_url}/file/bot{self.token}/{file_path}"

    async def send_media(
        self,
        *,
        chat_id: str,
        message_type: str,
        media_url: str,
        caption: str | None = None,
        file_name: str | None = None,
    ) -> dict[str, Any]:
        method, media_field = {
            "picture": ("sendPhoto", "photo"),
            "video": ("sendVideo", "video"),
            "voice": ("sendVoice", "voice"),
            "audio": ("sendAudio", "audio"),
        }.get(message_type, ("sendDocument", "document"))
        payload: dict[str, Any] = {"chat_id": chat_id, media_field: media_url}
        if caption:
            payload["caption"] = caption
        if file_name:
            payload["caption"] = caption or file_name
        return await self.request(method, payload)
