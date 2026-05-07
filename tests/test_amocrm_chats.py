import hashlib
import hmac

from tg_integration.amocrm_chats import (
    CONTENT_TYPE,
    build_request_signature,
    build_webhook_signature,
    compact_message_summary,
    encode_json_body,
    verify_webhook_signature,
)


def test_build_request_signature_matches_amocrm_algorithm() -> None:
    body = encode_json_body({"account_id": "account-id", "title": "TG", "hook_api_version": "v2"})
    date_header = "Mon, 03 Oct 2020 15:11:21 +0000"
    path = "/v2/origin/custom/channel-id/connect"
    secret = "secret"

    content_md5, signature = build_request_signature(
        method="POST",
        body=body,
        content_type=CONTENT_TYPE,
        date_header=date_header,
        path=path,
        secret=secret,
    )

    expected_md5 = hashlib.md5(body).hexdigest()
    signing_string = "\n".join(["POST", expected_md5, CONTENT_TYPE, date_header, path])
    expected_signature = hmac.new(secret.encode(), signing_string.encode(), hashlib.sha1).hexdigest()

    assert content_md5 == expected_md5
    assert signature == expected_signature


def test_verify_webhook_signature_uses_trimmed_raw_body() -> None:
    body = b'{"account_id":"account-id"}\n'
    secret = "secret"
    signature = build_webhook_signature(body=body, secret=secret)

    assert verify_webhook_signature(body=body, secret=secret, signature=signature)
    assert not verify_webhook_signature(body=body, secret="other", signature=signature)


def test_compact_message_summary_extracts_v2_fields() -> None:
    payload = {
        "account_id": "account-id",
        "message": {
            "conversation": {"id": "conversation-id", "client_id": "tg-chat-id"},
            "sender": {"id": "manager-id"},
            "receiver": {"id": "bot-id"},
            "timestamp": 1715000000,
            "message": {
                "id": "message-id",
                "type": "text",
                "text": "Hello",
            },
        },
    }

    assert compact_message_summary("scope-id", payload) == {
        "scope_id": "scope-id",
        "account_id": "account-id",
        "conversation_id": "conversation-id",
        "conversation_client_id": "tg-chat-id",
        "sender_id": "manager-id",
        "receiver_id": "bot-id",
        "message_id": "message-id",
        "message_type": "text",
        "text": "Hello",
        "media": None,
        "timestamp": 1715000000,
    }
