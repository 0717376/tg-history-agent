"""Telethon-индексатор: бэкфилл всей истории группы + живое дослушивание (new/edit/delete)."""

import asyncio
import logging

from telethon import TelegramClient, events, utils
from telethon.tl.functions.messages import GetForumTopicsRequest

from . import config, db

log = logging.getLogger("tgqa.indexer")

# Живой клиент/entity — для дозапросов из бота (снимок участников).
_client: TelegramClient | None = None
_entity = None
_members_at = 0.0
MEMBERS_MIN_INTERVAL = 600
MEMBERS_PERIOD = 12 * 3600
SWEEP_PERIOD = 300
SWEEP_DEPTH = 200


async def refresh_members(min_interval: float = MEMBERS_MIN_INTERVAL) -> None:
    """Снять снимок участников группы в таблицу members (не чаще min_interval)."""
    global _members_at
    import time as _time
    if _client is None or _time.monotonic() - _members_at < min_interval:
        return
    _members_at = _time.monotonic()
    try:
        users = await _client.get_participants(_entity)
    except Exception as e:
        log.warning("не удалось получить участников: %s", e)
        return
    db.replace_members([(u.id, utils.get_display_name(u)) for u in users])
    log.info("участников в снимке: %d", len(users))


async def _members_loop() -> None:
    while True:
        await asyncio.sleep(MEMBERS_PERIOD)
        await refresh_members(min_interval=0)


async def _sweep_loop(client: TelegramClient, entity) -> None:
    """Сверка хвоста истории: live-события Telethon иногда теряются."""
    while True:
        await asyncio.sleep(SWEEP_PERIOD)
        try:
            known = db.recent_ids(SWEEP_DEPTH)
            n = 0
            async for m in client.iter_messages(entity, limit=SWEEP_DEPTH):
                if m.id not in known and _store(m):
                    n += 1
            db.commit()
            if n:
                log.info("сверка: добрано %d пропущенных сообщений", n)
        except Exception as e:
            log.warning("сверка не удалась: %s", e)


def _target() -> str | int:
    t = config.TARGET_CHAT
    if not t:
        raise SystemExit("Не задан TG_TARGET_CHAT в .env (подобрать: uv run python -m app.chats)")
    return int(t) if t.lstrip("-").isdigit() else t


def _reply_to(m) -> int | None:
    rt = m.reply_to
    if rt is None:
        return None
    # В форуме reply-заголовок есть у каждого сообщения топика; реальный ответ
    # отличается заполненным reply_to_top_id.
    if getattr(rt, "forum_topic", False) and not rt.reply_to_top_id:
        return None
    return rt.reply_to_msg_id


def _topic_id(m) -> int | None:
    rt = m.reply_to
    if rt is not None and getattr(rt, "forum_topic", False):
        return rt.reply_to_top_id or rt.reply_to_msg_id
    return None


def _media_kind(m) -> str | None:
    for kind in ("photo", "sticker", "voice", "video_note", "video", "audio",
                 "document", "poll", "contact", "geo"):
        if getattr(m, kind, None) is not None:
            return kind
    return None


def _reactions(m) -> int:
    if not m.reactions or not m.reactions.results:
        return 0
    return sum(r.count for r in m.reactions.results)


def _store(m) -> bool:
    """Сохранить сообщение; сервисные и пустые без медиа — мимо. Без commit."""
    if m.action is not None:
        return False
    text = m.message or ""
    media = _media_kind(m)
    if not text and not media:
        return False
    sender = utils.get_display_name(m.sender) if m.sender else None
    filename = m.file.name if m.file else None
    db.upsert_message(m.id, m.date.strftime("%Y-%m-%d %H:%M:%S"), m.sender_id, sender,
                      _reply_to(m), _topic_id(m), media, filename, _reactions(m),
                      bool(m.pinned), text, commit=False)
    return True


async def _ingest(client: TelegramClient, entity, label: str, **iter_kwargs) -> int:
    n = 0
    async for m in client.iter_messages(entity, **iter_kwargs):
        if _store(m):
            n += 1
            if n % 200 == 0:
                db.commit()
            if n % 1000 == 0:
                log.info("%s: %d сообщений (id %d, %s)", label, n, m.id, m.date.date())
    db.commit()
    return n


async def _sync_topics(client: TelegramClient, entity) -> None:
    if not getattr(entity, "forum", False):
        return
    seen: set[int] = set()
    offset_topic = 0
    while True:
        res = await client(GetForumTopicsRequest(
            entity, offset_date=None, offset_id=0, offset_topic=offset_topic, limit=100))
        page = [t for t in res.topics if hasattr(t, "title") and t.id not in seen]
        if not page:
            break
        for t in page:
            seen.add(t.id)
            db.upsert_topic(t.id, t.title)
        if len(res.topics) < 100:
            break
        offset_topic = res.topics[-1].id
    log.info("топиков: %d", len(seen))


async def run() -> None:
    global _client, _entity
    client = TelegramClient(config.TG_SESSION, config.API_ID, config.API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("Нет Telethon-сессии — сначала: uv run python -m app.login")

    entity = await client.get_entity(_target())
    _client, _entity = client, entity
    db.meta_set("internal_chat_id", entity.id)
    db.meta_set("title", getattr(entity, "title", "") or "")
    db.meta_set("is_forum", "1" if getattr(entity, "forum", False) else "0")
    log.info("группа: «%s» (internal id %s, форум=%s)",
             getattr(entity, "title", "?"), entity.id, getattr(entity, "forum", False))

    async def on_new(event):
        if _store(event.message):
            db.commit()

    async def on_edit(event):
        if _store(event.message):
            db.commit()

    async def on_delete(event):
        for mid in event.deleted_ids:
            db.delete_message(mid)

    client.add_event_handler(on_new, events.NewMessage(chats=entity))
    client.add_event_handler(on_edit, events.MessageEdited(chats=entity))
    client.add_event_handler(on_delete, events.MessageDeleted(chats=entity))

    await _sync_topics(client, entity)
    await refresh_members(min_interval=0)
    loop = asyncio.get_running_loop()
    loop.create_task(_members_loop())
    loop.create_task(_sweep_loop(client, entity))

    mn, mx, total = db.bounds()
    if mx:  # догнать новое, появившееся пока индексатор не работал
        n = await _ingest(client, entity, "догон", min_id=mx)
        log.info("догон: +%d", n)
    if db.meta_get("backfill_done") != "1":
        kwargs = {"offset_id": mn} if mn else {}
        n = await _ingest(client, entity, "бэкфилл", **kwargs)
        db.meta_set("backfill_done", "1")
        log.info("бэкфилл завершён: +%d (всего %d)", n, db.bounds()[2])
    else:
        log.info("индекс актуален: %d сообщений", total)

    await client.run_until_disconnected()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(run())
