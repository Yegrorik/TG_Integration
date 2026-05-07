from __future__ import annotations

import json
import logging
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from .amocrm_chats import (
    AMOCRM_HOOK_VERSION,
    AmoCRMConnectPayload,
    compact_message_summary,
    connect_chat_channel,
    send_chat_event,
    update_delivery_status,
    verify_webhook_signature,
)
from .config import Settings, get_settings
from .storage import BridgeStore
from .telegram_bridge import (
    build_telegram_to_amocrm_message,
    extract_amocrm_outbound_message,
    extract_update_message,
    telegram_conversation_id,
    telegram_media_file,
    verify_telegram_webhook_secret,
)
from .telegram_client import TelegramAPIError, TelegramClient


logger = logging.getLogger(__name__)

app = FastAPI(title="TG Integration", version="0.1.0")


@app.middleware("http")
async def normalize_repeated_slashes(request: Request, call_next):
    path = request.scope.get("path", "")
    if "//" in path:
        normalized_path = "/" + "/".join(part for part in path.split("/") if part)
        request.scope["path"] = normalized_path
        request.scope["raw_path"] = normalized_path.encode("ascii")
    return await call_next(request)


class ConnectChannelRequest(BaseModel):
    account_id: str = Field(
        ...,
        description="amojo_id from /api/v4/account?with=amojo_id or APP.constant('account').amojo_id",
    )
    title: str | None = Field(default=None)
    hook_api_version: Literal["v1", "v2"] = Field(default=AMOCRM_HOOK_VERSION)
    is_time_window_disabled: bool | None = Field(default=None)


class ConnectChannelResponse(BaseModel):
    ok: bool
    status_code: int
    body: object


class WebhookAcceptedResponse(BaseModel):
    status: Literal["accepted"]
    scope_id: str


class TelegramWebhookAcceptedResponse(BaseModel):
    status: Literal["accepted", "ignored"]


class SetupTelegramWebhookRequest(BaseModel):
    public_base_url: str | None = Field(default=None)
    drop_pending_updates: bool = Field(default=False)


class SetupTelegramWebhookResponse(BaseModel):
    ok: bool
    webhook_url: str
    body: object


def require_chat_credentials(settings: Settings) -> tuple[str, str]:
    secret = require_chat_secret(settings)
    if not settings.amocrm_chat_channel_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AMOCRM_CHAT_CHANNEL_ID must be configured",
        )
    return settings.amocrm_chat_channel_id, secret


def require_chat_secret(settings: Settings) -> str:
    if not settings.amocrm_chat_channel_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AMOCRM_CHAT_CHANNEL_SECRET must be configured",
        )
    return settings.amocrm_chat_channel_secret


def require_chat_scope_id(settings: Settings) -> str:
    if not settings.amocrm_chat_scope_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AMOCRM_CHAT_SCOPE_ID must be configured",
        )
    return settings.amocrm_chat_scope_id


def require_telegram_credentials(settings: Settings) -> tuple[str, str]:
    if not settings.telegram_bot_token or not settings.telegram_webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET must be configured",
        )
    return settings.telegram_bot_token, settings.telegram_webhook_secret


def get_bridge_store(settings: Settings = Depends(get_settings)) -> BridgeStore:
    return BridgeStore(settings.bridge_db_path)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/amocrm/chats/connect", response_model=ConnectChannelResponse)
async def connect_amocrm_chat_channel(
    request: ConnectChannelRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    channel_id, secret = require_chat_credentials(settings)
    payload = AmoCRMConnectPayload(
        account_id=request.account_id,
        title=request.title or settings.amocrm_chat_default_title,
        hook_api_version=request.hook_api_version,
        is_time_window_disabled=request.is_time_window_disabled,
    )

    result = await connect_chat_channel(
        base_url=settings.amocrm_chat_base_url,
        channel_id=channel_id,
        secret=secret,
        payload=payload,
    )
    if not result["ok"]:
        raise HTTPException(status_code=int(result["status_code"]), detail=result["body"])
    return result


@app.post("/telegram/webhook/setup", response_model=SetupTelegramWebhookResponse)
async def setup_telegram_webhook(
    request: SetupTelegramWebhookRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    token, telegram_secret = require_telegram_credentials(settings)
    public_base_url = (request.public_base_url or settings.public_base_url or "").rstrip("/")
    if not public_base_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="PUBLIC_BASE_URL or request.public_base_url must be configured",
        )

    webhook_url = f"{public_base_url}/webhooks/telegram"
    client = TelegramClient(token=token, base_url=settings.telegram_api_base_url)
    body = await client.set_webhook(
        url=webhook_url,
        secret_token=telegram_secret,
        drop_pending_updates=request.drop_pending_updates,
    )
    return {"ok": True, "webhook_url": webhook_url, "body": body}


@app.post("/webhooks/telegram", response_model=TelegramWebhookAcceptedResponse)
async def telegram_webhook(
    request: Request,
    x_telegram_secret: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
    settings: Settings = Depends(get_settings),
    store: BridgeStore = Depends(get_bridge_store),
) -> dict[str, str]:
    token, telegram_secret = require_telegram_credentials(settings)
    if not verify_telegram_webhook_secret(
        configured_secret=telegram_secret,
        incoming_secret=x_telegram_secret,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-Telegram-Bot-Api-Secret-Token",
        )

    scope_id = require_chat_scope_id(settings)
    amo_secret = require_chat_secret(settings)
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body") from exc

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
        return {"status": "ignored"}
    if store.has_processed("telegram_update", inbound.event_id):
        return {"status": "accepted"}

    result = await send_chat_event(
        base_url=settings.amocrm_chat_base_url,
        scope_id=scope_id,
        secret=amo_secret,
        payload=inbound.amo_payload,
    )
    if not result["ok"]:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"amocrm": result["body"]},
        )

    response_body = result.get("body") if isinstance(result.get("body"), dict) else {}
    new_message = response_body.get("new_message") if isinstance(response_body, dict) else {}
    amo_conversation_id = (
        new_message.get("conversation_id")
        if isinstance(new_message, dict)
        else None
    ) or telegram_conversation_id(inbound.telegram_chat_id)
    amo_message_id = new_message.get("msgid") if isinstance(new_message, dict) else None
    amo_ref_id = new_message.get("ref_id") if isinstance(new_message, dict) else None

    store.upsert_conversation_link(
        telegram_chat_id=inbound.telegram_chat_id,
        amo_conversation_id=str(amo_conversation_id),
        telegram_user_id=inbound.telegram_user_id,
        telegram_name=inbound.telegram_name,
        telegram_username=inbound.telegram_username,
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
    return {"status": "accepted"}


@app.post("/webhooks/amocrm/chats/{scope_id}", response_model=WebhookAcceptedResponse)
async def amocrm_chats_webhook(
    scope_id: str,
    request: Request,
    x_signature: str | None = Header(default=None, alias="X-Signature"),
    settings: Settings = Depends(get_settings),
    store: BridgeStore = Depends(get_bridge_store),
) -> dict[str, str]:
    secret = require_chat_secret(settings)
    body = await request.body()

    if not verify_webhook_signature(body=body, secret=secret, signature=x_signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid X-Signature")

    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body") from exc

    summary = compact_message_summary(scope_id=scope_id, payload=payload)
    logger.info("Accepted amoCRM chat webhook", extra={"amocrm_chat": summary})

    configured_scope_id = settings.amocrm_chat_scope_id
    if configured_scope_id and configured_scope_id != scope_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown scope_id")

    token, _ = require_telegram_credentials(settings)
    outbound = extract_amocrm_outbound_message(payload)
    event_id = outbound.amo_message_id or f"{summary.get('conversation_id')}:{summary.get('timestamp')}"
    if store.has_processed("amocrm_message", event_id):
        return {"status": "accepted", "scope_id": scope_id}

    telegram_chat_id = outbound.telegram_chat_id
    if not telegram_chat_id and summary.get("conversation_id"):
        link = store.get_link_by_amo_conversation_id(str(summary["conversation_id"]))
        telegram_chat_id = link.telegram_chat_id if link else None
    if not telegram_chat_id:
        logger.warning(
            "Cannot forward amoCRM message to Telegram because chat mapping is missing",
            extra={"amocrm_chat": summary},
        )
        return {"status": "accepted", "scope_id": scope_id}

    telegram = TelegramClient(token=token, base_url=settings.telegram_api_base_url)
    try:
        if outbound.media_url:
            telegram_response = await telegram.send_media(
                chat_id=telegram_chat_id,
                message_type=outbound.message_type,
                media_url=outbound.media_url,
                caption=outbound.text or None,
                file_name=outbound.file_name,
            )
        elif outbound.text:
            telegram_response = await telegram.send_text(chat_id=telegram_chat_id, text=outbound.text)
        else:
            logger.info("amoCRM message had no text or media, nothing to forward")
            return {"status": "accepted", "scope_id": scope_id}
    except TelegramAPIError as exc:
        logger.exception("Failed to forward amoCRM message to Telegram")
        if outbound.amo_message_id:
            await update_delivery_status(
                base_url=settings.amocrm_chat_base_url,
                scope_id=scope_id,
                message_id=outbound.amo_message_id,
                secret=secret,
                status_code=-1,
                error_code=905,
                error=str(exc),
            )
        return {"status": "accepted", "scope_id": scope_id}

    telegram_result = telegram_response.get("result") if isinstance(telegram_response, dict) else {}
    telegram_message_id = telegram_result.get("message_id") if isinstance(telegram_result, dict) else None
    store.save_message_link(
        source="amocrm",
        source_message_id=event_id,
        telegram_chat_id=telegram_chat_id,
        telegram_message_id=str(telegram_message_id) if telegram_message_id else None,
        amo_message_id=outbound.amo_message_id,
    )
    store.mark_processed("amocrm_message", event_id)
    if outbound.amo_message_id:
        await update_delivery_status(
            base_url=settings.amocrm_chat_base_url,
            scope_id=scope_id,
            message_id=outbound.amo_message_id,
            secret=secret,
            status_code=1,
        )

    return {"status": "accepted", "scope_id": scope_id}
