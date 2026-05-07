from pathlib import Path
from uuid import uuid4

from tg_integration.amocrm_crm import (
    extract_chat_message_ids_from_events,
    extract_conversation_id_from_payload,
    extract_lead_id_from_payload,
)
from tg_integration.storage import BridgeStore


def make_store() -> tuple[BridgeStore, Path]:
    db_path = Path("data") / f"test-{uuid4().hex}.sqlite3"
    if db_path.exists():
        db_path.unlink()
    return BridgeStore(str(db_path)), db_path


def test_extract_chat_message_ids_from_events() -> None:
    response = {
        "_embedded": {
            "events": [
                {"value_after": [{"message": {"id": "message-1"}}]},
                {"value_after": [{"message_id": "message-2"}]},
                {"value_after": [{"other": "value"}]},
            ]
        }
    }

    assert extract_chat_message_ids_from_events(response) == ["message-1", "message-2"]


def test_extract_lead_id_from_entity_payload() -> None:
    payload = {
        "message": {
            "conversation": {"id": "conversation-id"},
            "entity_type": "lead",
            "entity_id": 123456,
        }
    }

    assert extract_lead_id_from_payload(payload) == "123456"
    assert extract_conversation_id_from_payload(payload) == "conversation-id"


def test_extract_lead_id_from_template_params() -> None:
    payload = {
        "message": {
            "conversation": {"id": "conversation-id"},
            "message": {
                "template": {
                    "params": [
                        {"key": "{{lead.id}}", "value": "123456"},
                    ]
                }
            },
        }
    }

    assert extract_lead_id_from_payload(payload) == "123456"
    assert extract_conversation_id_from_payload(payload) == "conversation-id"


def test_store_manual_lead_conversation_link() -> None:
    store, db_path = make_store()
    try:

        store.link_lead_to_conversation(
            lead_id="123",
            amo_conversation_id="conversation-id",
        )

        assert store.get_conversation_id_by_lead_id("123") == "conversation-id"
    finally:
        db_path.unlink(missing_ok=True)


def test_store_finds_conversation_by_amo_message_id() -> None:
    store, db_path = make_store()
    try:
        store.upsert_conversation_link(
            telegram_chat_id="777",
            amo_conversation_id="conversation-id",
            telegram_user_id="888",
            telegram_name="Test",
            telegram_username=None,
        )
        store.save_message_link(
            source="telegram",
            source_message_id="update-1",
            telegram_chat_id="777",
            telegram_message_id="10",
            amo_message_id="amo-message-id",
        )

        link = store.find_link_by_amo_message_ids(["amo-message-id"])

        assert link is not None
        assert link.amo_conversation_id == "conversation-id"
    finally:
        db_path.unlink(missing_ok=True)
