from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Any
from urllib.parse import quote


AMOCRM_HOOK_VERSION = "v2"
CONTENT_TYPE = "application/json"


@dataclass(frozen=True)
class AmoCRMConnectPayload:
    account_id: str
    title: str
    hook_api_version: str = AMOCRM_HOOK_VERSION
    is_time_window_disabled: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "account_id": self.account_id,
            "title": self.title,
            "hook_api_version": self.hook_api_version,
        }
        if self.is_time_window_disabled is not None:
            payload["is_time_window_disabled"] = self.is_time_window_disabled
        return payload


def encode_json_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def build_request_signature(
    *,
    method: str,
    body: bytes,
    content_type: str,
    date_header: str,
    path: str,
    secret: str,
) -> tuple[str, str]:
    content_md5 = hashlib.md5(body).hexdigest().lower()
    signing_string = "\n".join(
        [
            method.upper(),
            content_md5,
            content_type,
            date_header,
            path,
        ]
    )
    signature = hmac.new(
        secret.encode("utf-8"),
        signing_string.encode("utf-8"),
        hashlib.sha1,
    ).hexdigest().lower()
    return content_md5, signature


def build_webhook_signature(*, body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body.strip(), hashlib.sha1).hexdigest().lower()


def verify_webhook_signature(*, body: bytes, secret: str, signature: str | None) -> bool:
    if not signature:
        return False
    expected = build_webhook_signature(body=body, secret=secret)
    return hmac.compare_digest(expected, signature.lower())


def current_rfc2822_date() -> str:
    return format_datetime(datetime.now(timezone.utc))


async def connect_chat_channel(
    *,
    base_url: str,
    channel_id: str,
    secret: str,
    payload: AmoCRMConnectPayload,
    timeout: float = 10.0,
) -> dict[str, Any]:
    path = f"/v2/origin/custom/{channel_id}/connect"
    return await signed_json_request(
        base_url=base_url,
        path=path,
        method="POST",
        secret=secret,
        payload=payload.as_dict(),
        timeout=timeout,
    )


async def send_chat_event(
    *,
    base_url: str,
    scope_id: str,
    secret: str,
    payload: dict[str, Any],
    timeout: float = 10.0,
) -> dict[str, Any]:
    return await signed_json_request(
        base_url=base_url,
        path=f"/v2/origin/custom/{scope_id}",
        method="POST",
        secret=secret,
        payload=payload,
        timeout=timeout,
    )


async def update_delivery_status(
    *,
    base_url: str,
    scope_id: str,
    message_id: str,
    secret: str,
    status_code: int,
    error_code: int | None = None,
    error: str | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"status_code": status_code}
    if error_code is not None:
        payload["error_code"] = error_code
    if error:
        payload["error"] = error
    return await signed_json_request(
        base_url=base_url,
        path=f"/v2/origin/custom/{scope_id}/{message_id}/delivery_status",
        method="POST",
        secret=secret,
        payload=payload,
        timeout=timeout,
    )


async def get_chat_history(
    *,
    base_url: str,
    scope_id: str,
    conversation_id: str,
    secret: str,
    offset: int = 0,
    limit: int = 50,
    timeout: float = 10.0,
) -> dict[str, Any]:
    safe_conversation_id = quote(conversation_id, safe="")
    return await signed_request(
        base_url=base_url,
        path=f"/v2/origin/custom/{scope_id}/chats/{safe_conversation_id}/history",
        method="GET",
        secret=secret,
        body=b"",
        params={"offset": offset, "limit": min(limit, 50)},
        timeout=timeout,
    )


async def signed_json_request(
    *,
    base_url: str,
    path: str,
    method: str,
    secret: str,
    payload: dict[str, Any],
    timeout: float = 10.0,
) -> dict[str, Any]:
    body = encode_json_body(payload)
    return await signed_request(
        base_url=base_url,
        path=path,
        method=method,
        secret=secret,
        body=body,
        timeout=timeout,
    )


async def signed_request(
    *,
    base_url: str,
    path: str,
    method: str,
    secret: str,
    body: bytes,
    params: dict[str, int | str] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    import httpx

    date_header = current_rfc2822_date()
    content_md5, signature = build_request_signature(
        method=method,
        body=body,
        content_type=CONTENT_TYPE,
        date_header=date_header,
        path=path,
        secret=secret,
    )
    headers = {
        "Date": date_header,
        "Content-Type": CONTENT_TYPE,
        "Content-MD5": content_md5,
        "X-Signature": signature,
    }

    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout) as client:
        response = await client.request(method, path, content=body, params=params, headers=headers)

    response_text = response.text
    response_json: Any
    try:
        response_json = response.json()
    except json.JSONDecodeError:
        response_json = None

    if response.is_error:
        return {
            "ok": False,
            "status_code": response.status_code,
            "body": response_json if response_json is not None else response_text,
        }

    return {
        "ok": True,
        "status_code": response.status_code,
        "body": response_json if response_json is not None else response_text,
    }


def compact_message_summary(scope_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    message = payload.get("message") or {}
    message_body = message.get("message") or {}
    conversation = message.get("conversation") or {}
    sender = message.get("sender") or {}
    receiver = message.get("receiver") or {}

    return {
        "scope_id": scope_id,
        "account_id": payload.get("account_id"),
        "conversation_id": conversation.get("id"),
        "conversation_client_id": conversation.get("client_id"),
        "sender_id": sender.get("id"),
        "receiver_id": receiver.get("id"),
        "message_id": message_body.get("id"),
        "message_type": message_body.get("type"),
        "text": message_body.get("text"),
        "media": message_body.get("media"),
        "timestamp": message.get("timestamp"),
    }
