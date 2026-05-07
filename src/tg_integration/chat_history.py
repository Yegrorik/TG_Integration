from __future__ import annotations

import argparse
import asyncio
import json

from .amocrm_chats import get_chat_history
from .config import load_settings


async def fetch_history(*, conversation_id: str, offset: int, limit: int) -> dict[str, object]:
    settings = load_settings()
    if not settings.amocrm_chat_scope_id:
        raise RuntimeError("AMOCRM_CHAT_SCOPE_ID must be configured")
    if not settings.amocrm_chat_channel_secret:
        raise RuntimeError("AMOCRM_CHAT_CHANNEL_SECRET must be configured")

    result = await get_chat_history(
        base_url=settings.amocrm_chat_base_url,
        scope_id=settings.amocrm_chat_scope_id,
        conversation_id=conversation_id,
        secret=settings.amocrm_chat_channel_secret,
        offset=offset,
        limit=limit,
    )
    if not result["ok"]:
        raise RuntimeError(json.dumps(result, ensure_ascii=False))
    return {
        "conversation_id": conversation_id,
        "offset": offset,
        "limit": limit,
        "history": result["body"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch amoCRM Chats history by conversation_id.")
    parser.add_argument("conversation_id", help="amoCRM chat conversation_id, for example tg:123456789")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    result = asyncio.run(
        fetch_history(
            conversation_id=args.conversation_id,
            offset=args.offset,
            limit=args.limit,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
