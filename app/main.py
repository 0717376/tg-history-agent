"""Точка входа: индексатор (Telethon) + бот (long polling) в одном процессе."""

import asyncio
import logging

from . import bot, indexer


async def main() -> None:
    await asyncio.gather(indexer.run(), bot.run())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(main())
