from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .amocrm_chats import send_chat_event
from .amocrm_crm import extract_conversation_id_from_payload, extract_lead_id_from_payload
from .config import Settings
from .storage import BridgeStore
from .telegram_bridge import (
    build_telegram_to_amocrm_message,
    extract_update_message,
    telegram_conversation_id,
    telegram_media_file,
)
from .telegram_client import TelegramClient


@dataclass(frozen=True)
class TelegramUpdateProcessingResult:
    status: str


class BridgeConfigError(RuntimeError):
    pass


class BridgeUpstreamError(RuntimeError):
    def __init__(self, upstream: str, body: Any) -> None:
        super().__init__(f"{upstream} upstream error: {body}")
        self.upstream = upstream
        self.body = body


def require_bridge_settings(settings: Settings) -> tuple[str, str, str]:
    if not settings.telegram_bot_token:
        raise BridgeConfigError("TELEGRAM_BOT_TOKEN must be configured")
    if not settings.amocrm_chat_scope_id:
        raise BridgeConfigError("AMOCRM_CHAT_SCOPE_ID must be configured")
    if not settings.amocrm_chat_channel_secret:
        raise BridgeConfigError("AMOCRM_CHAT_CHANNEL_SECRET must be configured")
    return (
        settings.telegram_bot_token,
        settings.amocrm_chat_scope_id,
        settings.amocrm_chat_channel_secret,
    )


async def process_telegram_update(
    payload: dict[str, Any],
    *,
    settings: Settings,
    store: BridgeStore,
) -> TelegramUpdateProcessingResult:
    token, scope_id, amo_secret = require_bridge_settings(settings)

    media_url = None
    media_kind = None
    if settings.telegram_expose_file_urls:
        message, _ = extract_update_message(payload)
        file_info = telegram_media_file(message or {})
        if file_info:
            file_id, media_kind = file_info
            telegram = TelegramClient(token=token, base_url=settings.telegram_api_base_url)
            media_url = await telegram.get_file_url(file_id=file_id)

    inbound = build_telegram_to_amocrm_message(payload, media_url=media_url, media_kind=media_kind)
    if inbound is None:
        return TelegramUpdateProcessingResult(status="ignored")
    if store.has_processed("telegram_update", inbound.event_id):
        return TelegramUpdateProcessingResult(status="accepted")

    result = await send_chat_event(
        base_url=settings.amocrm_chat_base_url,
        scope_id=scope_id,
        secret=amo_secret,
        payload=inbound.amo_payload,
    )
    if not result["ok"]:
        raise BridgeUpstreamError("amocrm", result["body"])

    response_body = result.get("body") if isinstance(result.get("body"), dict) else {}
    response_lead_id = extract_lead_id_from_payload(response_body)
    new_message = response_body.get("new_message") if isinstance(response_body, dict) else {}
    amo_conversation_id = (
        new_message.get("conversation_id")
        if isinstance(new_message, dict)
        else None
    ) or extract_conversation_id_from_payload(response_body) or telegram_conversation_id(inbound.telegram_chat_id)
    amo_message_id = new_message.get("msgid") if isinstance(new_message, dict) else None
    amo_ref_id = new_message.get("ref_id") if isinstance(new_message, dict) else None

    store.upsert_conversation_link(
        telegram_chat_id=inbound.telegram_chat_id,
        amo_conversation_id=str(amo_conversation_id),
        telegram_user_id=inbound.telegram_user_id,
        telegram_name=inbound.telegram_name,
        telegram_username=inbound.telegram_username,
    )
    if response_lead_id:
        store.link_lead_to_conversation(
            lead_id=response_lead_id,
            amo_conversation_id=str(amo_conversation_id),
        )
    store.save_message_link(
        source="telegram",
        source_message_id=inbound.event_id,
        telegram_chat_id=inbound.telegram_chat_id,
        telegram_message_id=inbound.telegram_message_id,
        amo_message_id=str(amo_message_id) if amo_message_id else None,
        amo_ref_id=str(amo_ref_id) if amo_ref_id else None,
    )
    store.mark_processed("telegram_update", inbound.event_id)
    return TelegramUpdateProcessingResult(status="accepted")
