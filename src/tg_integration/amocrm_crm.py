from __future__ import annotations

from typing import Any


CHAT_EVENT_TYPES = (
    "incoming_chat_message",
    "outgoing_chat_message",
)


class AmoCRMAPIError(RuntimeError):
    def __init__(self, endpoint: str, status_code: int, body: Any) -> None:
        super().__init__(f"amoCRM API {endpoint} failed with HTTP {status_code}: {body}")
        self.endpoint = endpoint
        self.status_code = status_code
        self.body = body


class AmoCRMClient:
    def __init__(self, *, base_url: str, access_token: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.timeout = timeout

    async def get(self, endpoint: str, params: list[tuple[str, str]] | None = None) -> dict[str, Any]:
        import httpx

        headers = {"Authorization": f"Bearer {self.access_token}"}
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            response = await client.get(endpoint, params=params, headers=headers)
        try:
            body = response.json()
        except ValueError:
            body = response.text
        if response.is_error:
            raise AmoCRMAPIError(endpoint, response.status_code, body)
        return body

    async def list_lead_chat_events(
        self,
        *,
        lead_id: int | str,
        page: int = 1,
        limit: int = 100,
    ) -> dict[str, Any]:
        params: list[tuple[str, str]] = [
            ("filter[entity][]", "lead"),
            ("filter[entity_id][]", str(lead_id)),
            ("page", str(page)),
            ("limit", str(limit)),
        ]
        for event_type in CHAT_EVENT_TYPES:
            params.append(("filter[type][]", event_type))
        return await self.get("/api/v4/events", params=params)


def extract_chat_message_ids_from_events(events_response: dict[str, Any]) -> list[str]:
    embedded = events_response.get("_embedded") or {}
    events = embedded.get("events") or []
    message_ids: list[str] = []
    for event in events:
        value_after = event.get("value_after") or []
        for item in value_after:
            if isinstance(item, dict):
                message = item.get("message") or {}
                if isinstance(message, dict) and message.get("id") is not None:
                    message_ids.append(str(message["id"]))
                elif item.get("message_id") is not None:
                    message_ids.append(str(item["message_id"]))
    return message_ids


def extract_lead_id_from_payload(payload: Any) -> str | None:
    if isinstance(payload, dict):
        entity_type = payload.get("entity_type") or payload.get("entity")
        entity_id = payload.get("entity_id")
        if str(entity_type).lower() == "lead" and entity_id is not None:
            return str(entity_id)

        for key in ("lead_id", "leadId"):
            if payload.get(key) is not None:
                return str(payload[key])

        embedded = payload.get("_embedded")
        if isinstance(embedded, dict):
            leads = embedded.get("leads")
            if isinstance(leads, list) and leads:
                first_lead = leads[0]
                if isinstance(first_lead, dict) and first_lead.get("id") is not None:
                    return str(first_lead["id"])

        template = payload.get("template")
        if isinstance(template, dict):
            params = template.get("params")
            if isinstance(params, list):
                for item in params:
                    if not isinstance(item, dict):
                        continue
                    key = str(item.get("key") or "").strip().lower()
                    if key in {"{{lead.id}}", "lead.id", "lead_id"} and item.get("value") is not None:
                        return str(item["value"])

        for value in payload.values():
            lead_id = extract_lead_id_from_payload(value)
            if lead_id is not None:
                return lead_id

    if isinstance(payload, list):
        for item in payload:
            lead_id = extract_lead_id_from_payload(item)
            if lead_id is not None:
                return lead_id

    return None


def extract_conversation_id_from_payload(payload: Any) -> str | None:
    if isinstance(payload, dict):
        conversation = payload.get("conversation")
        if isinstance(conversation, dict) and conversation.get("id") is not None:
            return str(conversation["id"])

        for key in ("conversation_id", "chat_id"):
            if payload.get(key) is not None:
                return str(payload[key])

        new_message = payload.get("new_message")
        if isinstance(new_message, dict) and new_message.get("conversation_id") is not None:
            return str(new_message["conversation_id"])

        for value in payload.values():
            conversation_id = extract_conversation_id_from_payload(value)
            if conversation_id is not None:
                return conversation_id

    if isinstance(payload, list):
        for item in payload:
            conversation_id = extract_conversation_id_from_payload(item)
            if conversation_id is not None:
                return conversation_id

    return None
