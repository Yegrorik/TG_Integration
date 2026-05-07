from tg_integration.telegram_bridge import (
    build_telegram_to_amocrm_message,
    extract_amocrm_outbound_message,
    parse_telegram_chat_id,
    telegram_conversation_id,
    verify_telegram_webhook_secret,
)


def test_verify_telegram_webhook_secret() -> None:
    assert verify_telegram_webhook_secret(configured_secret="secret", incoming_secret="secret")
    assert not verify_telegram_webhook_secret(configured_secret="secret", incoming_secret="other")
    assert not verify_telegram_webhook_secret(configured_secret="secret", incoming_secret=None)


def test_build_telegram_to_amocrm_message_for_text_update() -> None:
    update = {
        "update_id": 42,
        "message": {
            "message_id": 77,
            "date": 1715000000,
            "chat": {"id": 123456, "type": "private", "first_name": "Ivan"},
            "from": {"id": 987, "first_name": "Ivan", "username": "ivan"},
            "text": "Hello",
        },
    }

    inbound = build_telegram_to_amocrm_message(update)

    assert inbound is not None
    assert inbound.event_id == "42"
    assert inbound.telegram_chat_id == "123456"
    assert inbound.amo_payload == {
        "event_type": "new_message",
        "payload": {
            "timestamp": 1715000000,
            "msec_timestamp": 1715000000000,
            "msgid": "tg:123456:77",
            "conversation_id": "tg:123456",
            "sender": {
                "id": "tg_user:987",
                "name": "Ivan",
                "profile_link": "https://t.me/ivan",
            },
            "message": {
                "type": "text",
                "text": "Hello",
            },
            "silent": False,
        },
    }


def test_extract_amocrm_outbound_message() -> None:
    payload = {
        "message": {
            "conversation": {"id": "amo-conversation-id", "client_id": "tg:123456"},
            "message": {"id": "amo-message-id", "type": "text", "text": "Hi from amo"},
        }
    }

    outbound = extract_amocrm_outbound_message(payload)

    assert outbound.telegram_chat_id == "123456"
    assert outbound.amo_message_id == "amo-message-id"
    assert outbound.text == "Hi from amo"


def test_telegram_id_helpers() -> None:
    assert telegram_conversation_id(123) == "tg:123"
    assert parse_telegram_chat_id("tg:123") == "123"
    assert parse_telegram_chat_id("other") is None
