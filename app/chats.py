"""Список групп/каналов аккаунта с id — чтобы выбрать TG_TARGET_CHAT."""

import asyncio

from telethon import TelegramClient

from . import config


async def main() -> None:
    client = TelegramClient(config.TG_SESSION, config.API_ID, config.API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("Нет Telethon-сессии — сначала: uv run python -m app.login")
    async for d in client.iter_dialogs():
        if d.is_group or d.is_channel:
            forum = " [форум]" if getattr(d.entity, "forum", False) else ""
            print(f"{d.id:>15}  {d.name}{forum}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
