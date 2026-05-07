from __future__ import annotations

import json
import logging
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from .amocrm_chats import (
    AMOCRM_HOOK_VERSION,
    AmoCRMConnectPayload,
    compact_message_summary,
    connect_chat_channel,
    get_chat_history,
    update_delivery_status,
    verify_webhook_signature,
)
from .amocrm_crm import extract_conversation_id_from_payload, extract_lead_id_from_payload
from .bridge_service import BridgeConfigError, BridgeUpstreamError, process_telegram_update
from .config import Settings, get_settings
from .lead_history import LeadHistoryConfigError, LeadHistoryNotFound, get_lead_chat_history
from .storage import BridgeStore
from .telegram_bridge import (
    extract_amocrm_outbound_message,
    parse_telegram_route,
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


class ConversationLinkRequest(BaseModel):
    conversation_id: str


class ConversationLinkResponse(BaseModel):
    lead_id: str
    conversation_id: str
    status: Literal["linked"]


class LeadChatHistoryResponse(BaseModel):
    lead_id: str
    conversation_id: str
    link_source: str
    crm_events_used: bool
    history: object


class ChatHistoryResponse(BaseModel):
    conversation_id: str
    offset: int
    limit: int
    history: object


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


@app.get("/amocrm/chats/{conversation_id}/history", response_model=ChatHistoryResponse)
async def amocrm_chat_history(
    conversation_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=50),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    scope_id = require_chat_scope_id(settings)
    secret = require_chat_secret(settings)
    result = await get_chat_history(
        base_url=settings.amocrm_chat_base_url,
        scope_id=scope_id,
        conversation_id=conversation_id,
        secret=secret,
        offset=offset,
        limit=limit,
    )
    if not result["ok"]:
        raise HTTPException(status_code=int(result["status_code"]), detail=result["body"])
    return {
        "conversation_id": conversation_id,
        "offset": offset,
        "limit": limit,
        "history": result["body"],
    }


@app.post("/amocrm/leads/{lead_id}/conversation-link", response_model=ConversationLinkResponse)
async def link_lead_to_conversation(
    lead_id: int,
    request: ConversationLinkRequest,
    store: BridgeStore = Depends(get_bridge_store),
) -> dict[str, str]:
    store.link_lead_to_conversation(
        lead_id=str(lead_id),
        amo_conversation_id=request.conversation_id,
    )
    return {
        "lead_id": str(lead_id),
        "conversation_id": request.conversation_id,
        "status": "linked",
    }


@app.get("/amocrm/leads/{lead_id}/chat-history", response_model=LeadChatHistoryResponse)
async def lead_chat_history(
    lead_id: int,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=50),
    settings: Settings = Depends(get_settings),
    store: BridgeStore = Depends(get_bridge_store),
) -> dict[str, object]:
    try:
        result = await get_lead_chat_history(
            lead_id=lead_id,
            settings=settings,
            store=store,
            offset=offset,
            limit=limit,
        )
    except LeadHistoryConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except LeadHistoryNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "message": str(exc),
                "message_ids_from_crm_events": exc.message_ids,
                "hint": "Link the lead to an amoCRM chat conversation first, or configure AMOCRM_ACCOUNT_BASE_URL and AMOCRM_ACCESS_TOKEN so the service can infer it from events.",
            },
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return {
        "lead_id": result.lead_id,
        "conversation_id": result.conversation_id,
        "link_source": result.link_source,
        "crm_events_used": result.crm_events_used,
        "history": result.history,
    }


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

    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body") from exc

    try:
        result = await process_telegram_update(payload, settings=settings, store=store)
    except BridgeConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except BridgeUpstreamError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={exc.upstream: exc.body},
        ) from exc
    return {"status": result.status}


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

    payload_lead_id = extract_lead_id_from_payload(payload)
    payload_conversation_id = extract_conversation_id_from_payload(payload)
    if payload_lead_id and payload_conversation_id:
        store.link_lead_to_conversation(
            lead_id=payload_lead_id,
            amo_conversation_id=payload_conversation_id,
        )
        logger.info(
            "Linked amoCRM lead %s to chat conversation %s",
            payload_lead_id,
            payload_conversation_id,
        )

    configured_scope_id = settings.amocrm_chat_scope_id
    if configured_scope_id and configured_scope_id != scope_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown scope_id")

    token, _ = require_telegram_credentials(settings)
    outbound = extract_amocrm_outbound_message(payload)
    event_id = outbound.amo_message_id or f"{summary.get('conversation_id')}:{summary.get('timestamp')}"
    if store.has_processed("amocrm_message", event_id):
        return {"status": "accepted", "scope_id": scope_id}

    telegram_chat_id = outbound.telegram_chat_id
    telegram_thread_id = outbound.telegram_thread_id
    if not telegram_chat_id and summary.get("conversation_id"):
        link = store.get_link_by_amo_conversation_id(str(summary["conversation_id"]))
        route = parse_telegram_route(link.telegram_chat_id) if link else None
        telegram_chat_id = route.chat_id if route else None
        telegram_thread_id = route.message_thread_id if route else None
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
                message_thread_id=telegram_thread_id,
            )
        elif outbound.text:
            telegram_response = await telegram.send_text(
                chat_id=telegram_chat_id,
                text=outbound.text,
                message_thread_id=telegram_thread_id,
            )
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
        telegram_chat_id=str(summary.get("conversation_client_id") or telegram_chat_id),
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
