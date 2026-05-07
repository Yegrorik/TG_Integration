from __future__ import annotations

import hmac
from dataclasses import dataclass
from time import time
from typing import Any


TELEGRAM_CHAT_PREFIX = "tg:"
TELEGRAM_USER_PREFIX = "tg_user:"


@dataclass(frozen=True)
class TelegramInboundMessage:
    event_id: str
    event_type: str
    telegram_chat_id: str
    telegram_message_id: str
    telegram_user_id: str | None
    telegram_name: str
    telegram_username: str | None
    amo_payload: dict[str, Any]


@dataclass(frozen=True)
class AmoOutboundMessage:
    amo_message_id: str | None
    telegram_chat_id: str | None
    message_type: str
    text: str
    media_url: str | None
    file_name: str | None


def verify_telegram_webhook_secret(*, configured_secret: str, incoming_secret: str | None) -> bool:
    if not incoming_secret:
        return False
    return hmac.compare_digest(configured_secret, incoming_secret)


def telegram_conversation_id(chat_id: int | str) -> str:
    return f"{TELEGRAM_CHAT_PREFIX}{chat_id}"


def parse_telegram_chat_id(value: str | None) -> str | None:
    if not value:
        return None
    if value.startswith(TELEGRAM_CHAT_PREFIX):
        return value[len(TELEGRAM_CHAT_PREFIX) :]
    return None


def telegram_user_id(user_id: int | str | None) -> str | None:
    if user_id is None:
        return None
    return f"{TELEGRAM_USER_PREFIX}{user_id}"


def telegram_full_name(user: dict[str, Any] | None, chat: dict[str, Any] | None = None) -> str:
    source = user or chat or {}
    title = source.get("title")
    if title:
        return str(title)
    parts = [source.get("first_name"), source.get("last_name")]
    name = " ".join(str(part) for part in parts if part)
    if name:
        return name
    username = source.get("username")
    if username:
        return f"@{username}"
    source_id = source.get("id")
    return f"Telegram {source_id}" if source_id is not None else "Telegram user"


def telegram_profile_link(username: str | None) -> str | None:
    if not username:
        return None
    return f"https://t.me/{username}"


def extract_update_message(update: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    if isinstance(update.get("message"), dict):
        return update["message"], False
    if isinstance(update.get("edited_message"), dict):
        return update["edited_message"], True
    return None, False


def describe_unsupported_telegram_message(message: dict[str, Any]) -> str:
    if "photo" in message:
        return "Telegram photo received. Enable TELEGRAM_EXPOSE_FILE_URLS to forward Telegram media URLs to amoCRM."
    for key in ("document", "video", "voice", "audio", "sticker"):
        if key in message:
            return f"Telegram {key} received. Enable TELEGRAM_EXPOSE_FILE_URLS to forward Telegram media URLs to amoCRM."
    if "contact" in message:
        contact = message["contact"]
        name = " ".join(
            str(part)
            for part in (contact.get("first_name"), contact.get("last_name"))
            if part
        )
        phone = contact.get("phone_number", "")
        return f"Telegram contact: {name} {phone}".strip()
    if "location" in message:
        location = message["location"]
        return f"Telegram location: {location.get('latitude')}, {location.get('longitude')}"
    return "Unsupported Telegram message type"


def telegram_media_file(message: dict[str, Any]) -> tuple[str, str] | None:
    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        photo = sorted(photos, key=lambda item: item.get("file_size", 0))[-1]
        return str(photo["file_id"]), "picture"
    for key, amo_kind in (
        ("document", "file"),
        ("video", "video"),
        ("voice", "voice"),
        ("audio", "audio"),
        ("sticker", "file"),
    ):
        item = message.get(key)
        if isinstance(item, dict) and item.get("file_id"):
            return str(item["file_id"]), amo_kind
    return None


def build_telegram_to_amocrm_message(
    update: dict[str, Any],
    *,
    media_url: str | None = None,
    media_kind: str | None = None,
) -> TelegramInboundMessage | None:
    message, is_edit = extract_update_message(update)
    if not message:
        return None

    chat = message.get("chat") or {}
    sender = message.get("from") or chat
    chat_id = chat.get("id")
    message_id = message.get("message_id")
    if chat_id is None or message_id is None:
        return None

    event_id = str(update.get("update_id", f"{chat_id}:{message_id}"))
    event_type = "edit_message" if is_edit else "new_message"
    unix_timestamp = int(message.get("date") or time())
    text = message.get("text") or message.get("caption") or describe_unsupported_telegram_message(message)
    msg_type = media_kind or ("file" if media_url else "text")
    username = sender.get("username")
    user_id = telegram_user_id(sender.get("id"))
    name = telegram_full_name(sender, chat)

    message_payload: dict[str, Any] = {
        "type": msg_type,
        "text": text,
    }
    if media_url:
        message_payload["media"] = media_url

    payload: dict[str, Any] = {
        "timestamp": unix_timestamp,
        "msec_timestamp": unix_timestamp * 1000,
        "msgid": f"tg:{chat_id}:{message_id}",
        "conversation_id": telegram_conversation_id(chat_id),
        "message": message_payload,
    }
    if not is_edit:
        payload["sender"] = {
            "id": user_id or telegram_conversation_id(chat_id),
            "name": name,
        }
        payload["silent"] = False
        profile_link = telegram_profile_link(username)
        if profile_link:
            payload["sender"]["profile_link"] = profile_link

    amo_payload: dict[str, Any] = {
        "event_type": event_type,
        "payload": payload,
    }

    return TelegramInboundMessage(
        event_id=event_id,
        event_type=event_type,
        telegram_chat_id=str(chat_id),
        telegram_message_id=str(message_id),
        telegram_user_id=str(sender.get("id")) if sender.get("id") is not None else None,
        telegram_name=name,
        telegram_username=str(username) if username else None,
        amo_payload=amo_payload,
    )


def extract_amocrm_outbound_message(payload: dict[str, Any]) -> AmoOutboundMessage:
    message_wrapper = payload.get("message") or {}
    conversation = message_wrapper.get("conversation") or {}
    message = message_wrapper.get("message") or {}
    conversation_client_id = conversation.get("client_id")

    return AmoOutboundMessage(
        amo_message_id=message.get("id"),
        telegram_chat_id=parse_telegram_chat_id(conversation_client_id),
        message_type=str(message.get("type") or "text"),
        text=str(message.get("text") or ""),
        media_url=message.get("media"),
        file_name=message.get("file_name"),
    )
