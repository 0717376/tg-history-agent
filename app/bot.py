"""Телеграм-бот (long polling): личка по allowlist + ответы в группе по @упоминанию.

Стриминг ответа — sendMessageDraft (Bot API 9.5) в личке, typing в группе;
код переноса markdown → HTML взят из Bender.
"""

import asyncio
import html as html_lib
import logging
import re
import time

import httpx

from . import agent, config, db

logger = logging.getLogger("tgqa.bot")

WELCOME = (
    "Привет! Я поисковый агент по истории группы.\n\n"
    "Спроси о чём-нибудь, что обсуждали в группе — я переберу историю "
    "(поиск, контекст, ветки ответов) и отвечу со ссылками на конкретные сообщения.\n\n"
    "Примеры:\n"
    "• что решили по схеме БД?\n"
    "• кто занимался нагрузочным тестированием и что выяснили?\n"
    "• где скидывали документацию по интеграциям?\n\n"
    "**Команды**\n"
    "/new — новая сессия (забыть контекст разговора)\n"
    "/status — состояние индекса\n"
    "/help — это сообщение"
)

DRAFT_CAP = 3500
DRAFT_THROTTLE = 1.0
TG_LIMIT = 3500


def md_to_tg_html(md: str) -> str:
    """Markdown → безопасное HTML-подмножество Telegram."""
    md = re.sub(r"^#{1,6}[ \t]+(.+?)\s*#*$", r"**\1**", md, flags=re.M)
    md = re.sub(r"^[ \t]*[-*][ \t]+", "• ", md, flags=re.M)

    stash: list[str] = []

    def keep(s: str) -> str:
        stash.append(s)
        return f"\x00{len(stash) - 1}\x00"

    md = re.sub(r"```[^\n]*\n(.*?)```",
                lambda m: keep(f"<pre>{html_lib.escape(m.group(1))}</pre>"), md, flags=re.S)
    md = re.sub(r"```(.*?)```",
                lambda m: keep(f"<pre>{html_lib.escape(m.group(1))}</pre>"), md, flags=re.S)
    md = re.sub(r"`([^`\n]+)`", lambda m: keep(f"<code>{html_lib.escape(m.group(1))}</code>"), md)

    md = html_lib.escape(md)
    md = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', md)
    md = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", md)
    md = re.sub(r"__([^_\n]+)__", r"<b>\1</b>", md)

    return re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], md)


def split_md(text: str, limit: int = TG_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks, buf = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(buf) + len(line) + 1 > limit:
            chunks.append(buf)
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        chunks.append(buf)
    return chunks


async def tg_api(client: httpx.AsyncClient, method: str, **params) -> dict:
    try:
        r = await client.post(f"{config.TG_API}/{method}", json=params)
        data = r.json()
        if not data.get("ok"):
            logger.warning("tg %s failed: %s", method, data.get("description"))
        return data
    except Exception as e:
        logger.warning("tg %s error: %s", method, e or type(e).__name__)
        return {"ok": False}


async def tg_send(client: httpx.AsyncClient, chat_id: int, text: str,
                  reply_to: int | None = None) -> None:
    for chunk in split_md(text):
        extra = {"reply_to_message_id": reply_to} if reply_to else {}
        res = await tg_api(client, "sendMessage", chat_id=chat_id, text=md_to_tg_html(chunk),
                           parse_mode="HTML", disable_web_page_preview=True, **extra)
        if not res.get("ok"):
            await tg_api(client, "sendMessage", chat_id=chat_id, text=chunk,
                         disable_web_page_preview=True, **extra)
        reply_to = None  # реплаем только первый чанк


async def tg_typing(client: httpx.AsyncClient, chat_id: int, stop: asyncio.Event,
                    thread_id: int | None = None) -> None:
    extra = {"message_thread_id": thread_id} if thread_id else {}
    while not stop.is_set():
        await tg_api(client, "sendChatAction", chat_id=chat_id, action="typing", **extra)
        try:
            await asyncio.wait_for(stop.wait(), timeout=4.5)
        except TimeoutError:
            pass


def group_chat_id() -> int | None:
    internal = db.meta_get("internal_chat_id")
    return int(f"-100{internal}") if internal else None


# user_id → (когда проверяли, участник ли). TTL, чтобы не дёргать API на каждое сообщение.
_member_cache: dict[int, tuple[float, bool]] = {}
MEMBER_TTL = 600


async def is_group_member(client: httpx.AsyncClient, user_id: int) -> bool:
    if db.is_member(user_id):
        return True
    hit = _member_cache.get(user_id)
    now = time.monotonic()
    if hit and now - hit[0] < MEMBER_TTL:
        return hit[1]
    # Промах по снимку: возможно, человек вступил недавно — обновляем снимок (с троттлингом).
    from . import indexer
    await indexer.refresh_members()
    if db.is_member(user_id):
        return True
    # Резерв: если бот сам состоит в группе, спросим Bot API.
    gid = group_chat_id()
    ok = False
    if gid:
        res = await tg_api(client, "getChatMember", chat_id=gid, user_id=user_id)
        ok = (res.get("result") or {}).get("status") in ("creator", "administrator", "member", "restricted")
    _member_cache[user_id] = (now, ok)
    return ok


def _who(user: dict | None) -> tuple[int | None, str]:
    u = user or {}
    name = " ".join(x for x in (u.get("first_name"), u.get("last_name")) if x)
    return u.get("id"), name or u.get("username") or "?"


def build_status(chat_id: int, owner: bool = False) -> str:
    s = db.stats()
    lines = [
        f"**Группа:** {s['title']}",
        f"**Сообщений в индексе:** {s['total']}",
        f"**Период:** {str(s['date_from'])[:10]} — {str(s['date_to'])[:16]}",
        f"**Бэкфилл:** {'завершён' if s['backfill_done'] else 'идёт'}",
    ]
    age = agent.session_age(chat_id)
    lines.append(f"**Сессия:** {'активна, посл. запрос ' + age if age else 'нет'}")
    if owner:
        u = db.usage_summary()
        lines.append(f"\n**Вопросов всего:** {u['total']} (за сутки {u['last_day']}"
                     + (f", с ошибкой {u['errors']}" if u["errors"] else "") + ")")
        for r in u["top"]:
            lines.append(f"• {r['name'] or r['user_id']} — {r['n']}, посл. {str(r['last'])[:16]}")
    return "\n".join(lines)


async def _answer(client: httpx.AsyncClient, chat_id: int, text: str,
                  user: dict | None = None, scope: str = "private",
                  reply_to: int | None = None, thread_id: int | None = None,
                  use_draft: bool = True) -> None:
    uid, name = _who(user)
    logger.info("вопрос [%s] от %s (%s): %s", scope, name, uid, text[:150].replace("\n", " "))
    started = time.monotonic()
    stop = asyncio.Event()
    typing = asyncio.create_task(tg_typing(client, chat_id, stop, thread_id))
    draft = {"at": 0.0, "ok": None, "capped": False,
             "id": (time.time_ns() % 2_000_000_000) or 1}

    async def on_delta(acc: str) -> None:
        if not use_draft or draft["ok"] is False or draft["capped"] or not acc.strip():
            return
        now = time.monotonic()
        if now - draft["at"] < DRAFT_THROTTLE:
            return
        draft["at"] = now
        res = await tg_api(client, "sendMessageDraft", chat_id=chat_id,
                           draft_id=draft["id"], text=acc[:DRAFT_CAP])
        if draft["ok"] is None:
            draft["ok"] = bool(res.get("ok"))
        draft["capped"] = len(acc) > DRAFT_CAP

    async def on_tool(name: str, detail: str) -> None:
        logger.info("tool %s(%s)", name, detail[:60])

    try:
        reply = await agent.run_collect(chat_id, text, on_tool=on_tool, on_delta=on_delta)
    finally:
        stop.set()
        await typing
    secs = time.monotonic() - started
    ok = reply not in (agent.ERR_RUN, agent.ERR_SESSION)
    logger.info("ответ %s: %d символов за %.0fс", "ok" if ok else "ОШИБКА", len(reply), secs)
    db.log_usage(uid, name, scope, text, secs, ok)
    await tg_send(client, chat_id, reply, reply_to=reply_to)


async def _handle_private(client: httpx.AsyncClient, msg: dict) -> None:
    chat_id = msg["chat"]["id"]
    user_id = (msg.get("from") or {}).get("id")
    if not config.ALLOWED_IDS and not config.ALLOW_GROUP_MEMBERS:
        await tg_api(client, "sendMessage", chat_id=chat_id,
                     text=f"Бот ещё не настроен. Ваш Telegram ID: {user_id}\n"
                          f"Добавьте его в TG_ALLOWED_IDS и перезапустите.")
        return
    if user_id not in config.ALLOWED_IDS and \
            not (config.ALLOW_GROUP_MEMBERS and await is_group_member(client, user_id)):
        await tg_api(client, "sendMessage", chat_id=chat_id,
                     text="Этот бот — только для участников группы.")
        return
    text = (msg.get("text") or "").strip()
    if not text:
        await tg_api(client, "sendMessage", chat_id=chat_id,
                     text="Я понимаю только текст — голос и файлы не разбираю.")
        return
    if text in ("/start", "/help"):
        await tg_send(client, chat_id, WELCOME)
    elif text in ("/new", "/clear"):
        agent.clear_session(chat_id)
        await tg_api(client, "sendMessage", chat_id=chat_id, text="Начал новую сессию.")
    elif text == "/status":
        await tg_send(client, chat_id, build_status(chat_id, owner=user_id in config.ALLOWED_IDS))
    else:
        await _answer(client, chat_id, text, user=msg.get("from"))


async def _handle_group(client: httpx.AsyncClient, msg: dict, bot_username: str,
                        bot_id: int) -> None:
    text = (msg.get("text") or "").strip()
    mention = f"@{bot_username}"
    replied = msg.get("reply_to_message") or {}
    is_reply_to_bot = (replied.get("from") or {}).get("id") == bot_id
    if mention.lower() not in text.lower() and not is_reply_to_bot:
        return
    q = re.sub(re.escape(mention), "", text, flags=re.I).strip()
    if is_reply_to_bot and replied.get("text"):
        q = f"[В ответ на твоё сообщение: {replied['text'][:500]}]\n{q}"
    if not q:
        return
    chat_id = msg["chat"]["id"]
    sender = (msg.get("from") or {}).get("first_name") or ""
    await _answer(client, chat_id, f"[Вопрос от {sender} в группе]\n{q}",
                  user=msg.get("from"), scope="group",
                  reply_to=msg["message_id"], thread_id=msg.get("message_thread_id"),
                  use_draft=False)


BOT_COMMANDS = [
    {"command": "status", "description": "Состояние индекса и сессии"},
    {"command": "new", "description": "Новая сессия (забыть контекст разговора)"},
    {"command": "help", "description": "Что умеет бот"},
]


async def run() -> None:
    if not config.BOT_TOKEN:
        logger.warning("TG_BOT_TOKEN не задан — бот выключен")
        return
    # Long poll 25с (не 60): rootless-докер (slirp4netns) дропает долгие idle-соединения.
    async with httpx.AsyncClient(timeout=40) as client:
        await tg_api(client, "setMyCommands", commands=BOT_COMMANDS)  # меню «/» в клиентах
        me = (await tg_api(client, "getMe")).get("result") or {}
        bot_username, bot_id = me.get("username", ""), me.get("id", 0)
        logger.info("бот @%s запущен", bot_username)
        offset = 0
        while True:
            data = await tg_api(client, "getUpdates", offset=offset, timeout=25,
                                allowed_updates=["message"])
            for upd in data.get("result") or []:
                offset = max(offset, upd["update_id"] + 1)
                msg = upd.get("message")
                if not msg or not msg.get("from") or msg["from"].get("is_bot"):
                    continue
                try:
                    if msg["chat"]["type"] == "private":
                        await _handle_private(client, msg)
                    elif msg["chat"]["id"] == group_chat_id():
                        await _handle_group(client, msg, bot_username, bot_id)
                except Exception:
                    logger.exception("update failed")
            if not data.get("ok"):
                await asyncio.sleep(3)
