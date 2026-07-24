"""Двухшаговый вход в Telegram (без интерактивного stdin), создаёт data/user.session.

  1) python -m app.login +79991234567           — отправить код
  2) python -m app.login +79991234567 12345     — войти по коду
     python -m app.login +79991234567 12345 пароль2FA   — если включён облачный пароль
"""

import asyncio
import json
import sys

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from . import config

HASH_FILE = config.DATA_DIR / "login_code_hash.json"


async def main(phone: str, code: str | None, password: str | None) -> None:
    if not config.API_ID or not config.API_HASH:
        raise SystemExit("Заполните TG_API_ID и TG_API_HASH в .env")
    client = TelegramClient(config.TG_SESSION, config.API_ID, config.API_HASH)
    await client.connect()
    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"Уже авторизованы: {me.first_name} (@{me.username})")
    elif code is None:
        sent = await client.send_code_request(phone)
        HASH_FILE.write_text(json.dumps({"phone": phone, "hash": sent.phone_code_hash}))
        print("Код отправлен (смотрите Telegram/SMS). Теперь:")
        print(f"  uv run python -m app.login {phone} <код>")
    else:
        stash = json.loads(HASH_FILE.read_text())
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=stash["hash"])
        except SessionPasswordNeededError:
            if not password:
                raise SystemExit("Включён облачный пароль (2FA) — добавьте его третьим аргументом") from None
            await client.sign_in(password=password)
        HASH_FILE.unlink(missing_ok=True)
        me = await client.get_me()
        print(f"OK: вошли как {me.first_name} (@{me.username}), сессия — {config.TG_SESSION}.session")
    await client.disconnect()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None,
                     sys.argv[3] if len(sys.argv) > 3 else None))
