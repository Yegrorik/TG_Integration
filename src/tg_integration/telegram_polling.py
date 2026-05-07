from __future__ import annotations

import argparse
import asyncio
import logging

from .bridge_service import process_telegram_update
from .config import get_settings
from .storage import BridgeStore
from .telegram_client import TelegramClient


logger = logging.getLogger(__name__)


async def run_polling(*, drop_pending_updates: bool, once: bool, timeout: int) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN must be configured")

    store = BridgeStore(settings.bridge_db_path)
    telegram = TelegramClient(
        token=settings.telegram_bot_token,
        base_url=settings.telegram_api_base_url,
        timeout=timeout + 10,
    )
    await telegram.delete_webhook(drop_pending_updates=drop_pending_updates)
    logger.info("Telegram webhook disabled; polling started")

    offset: int | None = None
    while True:
        updates = await telegram.get_updates(offset=offset, timeout=timeout)
        if not updates and once:
            return

        for update in updates:
            update_id = update.get("update_id")
            try:
                result = await process_telegram_update(update, settings=settings, store=store)
            except Exception:
                logger.exception("Failed to process Telegram update %s", update_id)
                continue

            logger.info("Telegram update %s processed as %s", update_id, result.status)
            if isinstance(update_id, int):
                offset = update_id + 1

        if once:
            return


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll Telegram updates and forward them to amoCRM.")
    parser.add_argument(
        "--drop-pending-updates",
        action="store_true",
        help="Ask Telegram to drop queued updates when disabling webhook.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process currently available updates once and exit.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=25,
        help="Long polling timeout in seconds.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(
        run_polling(
            drop_pending_updates=args.drop_pending_updates,
            once=args.once,
            timeout=args.timeout,
        )
    )


if __name__ == "__main__":
    main()
