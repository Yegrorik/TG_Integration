from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .amocrm_chats import get_chat_history
from .amocrm_crm import AmoCRMClient, extract_chat_message_ids_from_events
from .config import Settings
from .storage import BridgeStore, ConversationLink


@dataclass(frozen=True)
class LeadHistoryResult:
    lead_id: str
    conversation_id: str
    history: Any
    link_source: str
    crm_events_used: bool


class LeadHistoryNotFound(RuntimeError):
    def __init__(self, lead_id: str, message_ids: list[str] | None = None) -> None:
        super().__init__(f"No locally linked chat conversation found for lead {lead_id}")
        self.lead_id = lead_id
        self.message_ids = message_ids or []


class LeadHistoryConfigError(RuntimeError):
    pass


def require_chat_history_settings(settings: Settings) -> tuple[str, str, str]:
    if not settings.amocrm_chat_scope_id:
        raise LeadHistoryConfigError("AMOCRM_CHAT_SCOPE_ID must be configured")
    if not settings.amocrm_chat_channel_secret:
        raise LeadHistoryConfigError("AMOCRM_CHAT_CHANNEL_SECRET must be configured")
    return (
        settings.amocrm_chat_base_url,
        settings.amocrm_chat_scope_id,
        settings.amocrm_chat_channel_secret,
    )


def crm_client_or_none(settings: Settings) -> AmoCRMClient | None:
    if not settings.amocrm_account_base_url or not settings.amocrm_access_token:
        return None
    return AmoCRMClient(
        base_url=settings.amocrm_account_base_url,
        access_token=settings.amocrm_access_token,
    )


async def infer_link_from_lead_events(
    *,
    lead_id: str,
    settings: Settings,
    store: BridgeStore,
) -> tuple[ConversationLink | None, list[str]]:
    client = crm_client_or_none(settings)
    if client is None:
        return None, []

    events_response = await client.list_lead_chat_events(lead_id=lead_id)
    message_ids = extract_chat_message_ids_from_events(events_response)
    link = store.find_link_by_amo_message_ids(message_ids)
    if link is not None:
        link = store.link_lead_to_conversation(
            lead_id=lead_id,
            amo_conversation_id=link.amo_conversation_id,
        )
    return link, message_ids


async def get_lead_chat_history(
    *,
    lead_id: int | str,
    settings: Settings,
    store: BridgeStore,
    offset: int = 0,
    limit: int = 50,
) -> LeadHistoryResult:
    lead_id_str = str(lead_id)
    base_url, scope_id, secret = require_chat_history_settings(settings)

    link_source = "local"
    crm_events_used = False
    conversation_id = store.get_conversation_id_by_lead_id(lead_id_str)
    message_ids: list[str] = []
    if conversation_id is None:
        link: ConversationLink | None
        link, message_ids = await infer_link_from_lead_events(
            lead_id=lead_id_str,
            settings=settings,
            store=store,
        )
        link_source = "crm_events"
        crm_events_used = True
        conversation_id = link.amo_conversation_id if link is not None else None

    if conversation_id is None:
        raise LeadHistoryNotFound(lead_id_str, message_ids=message_ids)

    result = await get_chat_history(
        base_url=base_url,
        scope_id=scope_id,
        conversation_id=conversation_id,
        secret=secret,
        offset=offset,
        limit=limit,
    )
    if not result["ok"]:
        raise RuntimeError(result["body"])

    return LeadHistoryResult(
        lead_id=lead_id_str,
        conversation_id=conversation_id,
        history=result["body"],
        link_source=link_source,
        crm_events_used=crm_events_used,
    )
