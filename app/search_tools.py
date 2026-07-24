"""Поисковые тулы агента (mcp__tg__*) — in-process SDK MCP-сервер, тонкие обёртки над db."""

import json

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import db


def _text(obj) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(obj, ensure_ascii=False, default=str)}]}


FILTERS = {
    "topic": {"type": "integer", "description": "id топика (см. topics)"},
    "sender": {"type": "string", "description": "автор (подстрока имени)"},
    "date_from": {"type": "string", "description": "YYYY-MM-DD"},
    "date_to": {"type": "string", "description": "YYYY-MM-DD"},
}


@tool("stats", "Обзор индекса: объём, период, топ авторов, топики, статус бэкфилла.",
      {"type": "object", "properties": {}})
async def stats(args):
    return _text(db.stats())


@tool("topics", "Топики форума: id, название, число сообщений, последняя активность.",
      {"type": "object", "properties": {}})
async def topics(args):
    return _text(db.stats()["topics"])


@tool("senders", "Участники: имя, число сообщений, первая/последняя активность. query — фильтр по имени.",
      {"type": "object", "properties": {"query": {"type": "string"}}})
async def senders(args):
    return _text(db.senders(args.get("query")))


@tool(
    "search",
    "Полнотекстовый поиск (SQLite FTS5). Синтаксис query: слова (неявное И), OR, \"точная фраза\", "
    "NEAR(a b, N). Морфология НЕ стеммится — для русского используй префиксы со звёздочкой: "
    "депло*, миграц*, паден*. Пробуй несколько вариантов запроса и синонимы. "
    "order: relevance | date_desc | date_asc. В хитах — готовые ссылки на сообщения.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            **FILTERS,
            "order": {"type": "string", "enum": ["relevance", "date_desc", "date_asc"]},
            "limit": {"type": "integer", "description": "default 20"},
            "offset": {"type": "integer", "description": "пагинация"},
        },
        "required": ["query"],
    },
)
async def search(args):
    return _text(db.search(args["query"], topic=args.get("topic"), sender=args.get("sender"),
                           date_from=args.get("date_from"), date_to=args.get("date_to"),
                           order=args.get("order", "relevance"),
                           limit=args.get("limit", 20), offset=args.get("offset", 0)))


@tool(
    "grep",
    "Точный поиск подстроки (без токенизации): коды ошибок, URL, имена функций/файлов, части слов. "
    "Дополняет search там, где словарный поиск бессилен.",
    {
        "type": "object",
        "properties": {
            "substring": {"type": "string"},
            **FILTERS,
            "limit": {"type": "integer"},
            "offset": {"type": "integer"},
        },
        "required": ["substring"],
    },
)
async def grep(args):
    return _text(db.grep(args["substring"], topic=args.get("topic"), sender=args.get("sender"),
                         date_from=args.get("date_from"), date_to=args.get("date_to"),
                         limit=args.get("limit", 20), offset=args.get("offset", 0)))


@tool(
    "read",
    "Прочитать сообщения полным текстом: либо список ids, либо диапазон from_id..to_id (до 100).",
    {
        "type": "object",
        "properties": {
            "ids": {"type": "array", "items": {"type": "integer"}},
            "from_id": {"type": "integer"},
            "to_id": {"type": "integer"},
        },
    },
)
async def read(args):
    return _text(db.read(ids=args.get("ids"), from_id=args.get("from_id"), to_id=args.get("to_id")))


@tool(
    "context",
    "Переписка вокруг сообщения (тот же топик): before сообщений до и after после. "
    "Обязательно проверяй контекст неоднозначных находок перед цитированием.",
    {
        "type": "object",
        "properties": {
            "message_id": {"type": "integer"},
            "before": {"type": "integer", "description": "default 10"},
            "after": {"type": "integer", "description": "default 10"},
        },
        "required": ["message_id"],
    },
)
async def context(args):
    return _text(db.context(args["message_id"], args.get("before", 10), args.get("after", 10)))


@tool("thread", "Вся ветка ответов вокруг сообщения: вверх до корня + все ответы вниз.",
      {"type": "object", "properties": {"message_id": {"type": "integer"}}, "required": ["message_id"]})
async def thread(args):
    return _text(db.thread(args["message_id"]))


@tool("pinned", "Закреплённые сообщения группы — обычно решения и важные ссылки.",
      {"type": "object", "properties": {}})
async def pinned(args):
    return _text(db.pinned_msgs())


@tool("files", "Файлы (документы) группы: поиск по имени файла или подписи.",
      {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}})
async def files(args):
    return _text(db.files(args.get("query"), args.get("limit", 30)))


@tool("top_reacted", "Сообщения с наибольшим числом реакций — что команда посчитала важным/смешным.",
      {"type": "object", "properties": {"topic": FILTERS["topic"], "date_from": FILTERS["date_from"],
                                        "date_to": FILTERS["date_to"], "limit": {"type": "integer"}}})
async def top_reacted(args):
    return _text(db.top_reacted(topic=args.get("topic"), date_from=args.get("date_from"),
                                date_to=args.get("date_to"), limit=args.get("limit", 15)))


@tool(
    "activity",
    "Гистограмма активности по дням/неделям (число сообщений, диапазон id) — найти пики "
    "обсуждений и копнуть в них через read/search с датами.",
    {
        "type": "object",
        "properties": {
            "date_from": FILTERS["date_from"], "date_to": FILTERS["date_to"],
            "topic": FILTERS["topic"],
            "granularity": {"type": "string", "enum": ["day", "week"]},
        },
    },
)
async def activity(args):
    return _text(db.activity(date_from=args.get("date_from"), date_to=args.get("date_to"),
                             topic=args.get("topic"), granularity=args.get("granularity", "day")))


TOOLS = [stats, topics, senders, search, grep, read, context, thread, pinned, files,
         top_reacted, activity]
TOOL_NAMES = [f"mcp__tg__{t.name}" for t in TOOLS]

server = create_sdk_mcp_server("tg", version="1.0.0", tools=TOOLS)
