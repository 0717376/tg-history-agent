"""Поисковый агент на Claude Agent SDK: per-chat сессии, только тулы mcp__tg__*.

Работает на OAuth Claude CLI (как Bender), без API-ключей.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolUseBlock,
    query,
)

from . import config, db
from .search_tools import TOOL_NAMES, server

logger = logging.getLogger("tgqa.agent")

# Одна очередь на все чаты: turn'ы не гоняем параллельно.
lock = asyncio.Lock()

ERR_RUN = "Что-то пошло не так при обработке запроса."
ERR_SESSION = "Ошибка Claude (возможно, переполнен контекст). Сессия сброшена — повторите вопрос."


# --- per-chat сессии (data/agent_sessions.json) ---

def _load_all() -> dict:
    try:
        return json.loads(config.AGENT_SESSIONS.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_all(d: dict) -> None:
    config.AGENT_SESSIONS.write_text(json.dumps(d))


def load_session(chat_id: int) -> str | None:
    e = _load_all().get(str(chat_id))
    if not e:
        return None
    if config.SESSION_FRESH_HOURS > 0:
        try:
            idle = datetime.now() - datetime.fromisoformat(e["last_used"])
            if idle > timedelta(hours=config.SESSION_FRESH_HOURS):
                return None
        except (KeyError, ValueError):
            return None
    return e.get("session_id")


def save_session(chat_id: int, session_id: str | None) -> None:
    if not session_id:
        return
    d = _load_all()
    d[str(chat_id)] = {"session_id": session_id,
                       "last_used": datetime.now().isoformat(timespec="seconds")}
    _save_all(d)


def clear_session(chat_id: int) -> None:
    d = _load_all()
    if d.pop(str(chat_id), None) is not None:
        _save_all(d)


def session_age(chat_id: int) -> str | None:
    e = _load_all().get(str(chat_id))
    if not e:
        return None
    mins = int((datetime.now() - datetime.fromisoformat(e["last_used"])).total_seconds() // 60)
    return f"{mins // 60}ч {mins % 60}м назад" if mins >= 60 else f"{mins}м назад"


# --- system prompt (заморожен на время сессии, чтобы не ломать prompt-кеш) ---

def _build_system() -> str:
    s = db.stats()
    topics = "\n".join(f"- [{t['id']}] {t['title']} — {t['n']} сообщ." for t in s["topics"] if t["n"])
    return f"""Ты — поисковый агент по истории Telegram-группы «{s['title']}» \
({s['total']} сообщений, {str(s['date_from'])[:10]} — {str(s['date_to'])[:10]}). \
Твоя единственная задача: находить в истории группы обсуждения по вопросу пользователя \
и отвечать со ссылками на конкретные сообщения.

Топики группы:
{topics}
Сообщения без топика — General и период до включения форума.

Метод (поиск итеративный — не сдавайся после первого запроса):
1. Разбей вопрос на ключевые слова; подбери синонимы и RU/EN-варианты терминов.
2. search — FTS без русской морфологии: префиксы со звёздочкой (депло*, миграц*, паден*), \
OR-варианты, "точные фразы". Несколько разных запросов лучше одного.
3. grep — точные подстроки: коды ошибок, URL, имена функций и файлов, части слов.
4. Зацепку раскрывай: context (что вокруг), thread (ветка ответов), read (кусок целиком). \
Неоднозначные находки не цитируй без проверки контекста.
5. Сужай фильтрами topic/sender/дат; activity покажет пики обсуждений; pinned и \
top_reacted — что команда считала важным.

Ответ:
- Сжато, по-русски, по делу.
- Каждое утверждение — со ссылкой: [дата, автор](https://t.me/c/...). Ссылки уже готовые \
в результатах тулов, работают у участников группы.
- НИКОГДА не упоминай голый id сообщения (типа «в 4551») — всегда оформляй ссылкой \
[описание](https://t.me/c/...); внутренние id без ссылки читателю бесполезны.
- Несколько обсуждений — группируй по смыслу или хронологии.
- Не выдумывай. Не нашлось — так и скажи и предложи переформулировку.
- Формат — телеграм-markdown: **жирный**, списки; заголовки # не использовать."""


_sys = {"key": "unset", "text": ""}


def _system_prompt(resume: str | None) -> str:
    if resume is None or resume != _sys["key"]:
        _sys["key"] = resume
        _sys["text"] = _build_system()
    return _sys["text"]


def build_options(resume: str | None) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        model=config.CLAUDE_MODEL,
        system_prompt=_system_prompt(resume),
        tools=[],  # без встроенных тулов Claude Code: allowed_tools набор не сужает
        allowed_tools=TOOL_NAMES,
        mcp_servers={"tg": server},
        strict_mcp_config=True,
        permission_mode="dontAsk",
        include_partial_messages=True,
        resume=resume,
        cwd=str(config.ROOT),
        setting_sources=None,
    )


def _is_stale(e: Exception) -> bool:
    return "No conversation found with session" in str(e)


async def run_collect(chat_id: int, message: str,
                      on_tool: Callable[[str, str], Awaitable[None]] | None = None,
                      on_delta: Callable[[str], Awaitable[None]] | None = None) -> str:
    """Один turn агента для чата chat_id; возвращает полный ответ.
    on_delta получает накопленный текст по мере стриминга (для драфтов)."""
    async with lock:
        message = f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}]\n{message}"
        for attempt in (1, 2):  # вторая попытка — только после сброса протухшей сессии
            sid = load_session(chat_id)
            texts: list[str] = []
            partial = ""
            result_text = ""
            final_sid = sid
            had_error = False
            try:
                async for m in query(prompt=message, options=build_options(sid)):
                    if isinstance(m, StreamEvent) and on_delta:
                        ev = m.event
                        delta = ev.get("delta", {}) if ev.get("type") == "content_block_delta" else {}
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            partial += delta["text"]
                            try:
                                await on_delta("\n\n".join([*texts, partial]))
                            except Exception:
                                pass
                    elif isinstance(m, AssistantMessage):
                        partial = ""
                        for block in m.content:
                            if isinstance(block, TextBlock) and block.text:
                                texts.append(block.text)
                            elif isinstance(block, ToolUseBlock) and on_tool:
                                inp = block.input or {}
                                detail = inp.get("query") or inp.get("substring") or \
                                    str(inp.get("message_id") or "")
                                try:
                                    await on_tool(block.name or "", detail)
                                except Exception:
                                    pass
                    elif isinstance(m, ResultMessage):
                        final_sid = m.session_id or sid
                        had_error = m.is_error
                        if not m.is_error and m.result:
                            result_text = m.result
            except Exception as e:
                # Ретрай без resume: сессия могла остаться от другого хоста (CLI падает
                # ProcessError'ом, текст «No conversation found» не всегда долетает).
                if attempt == 1 and sid and (_is_stale(e) or type(e).__name__ == "ProcessError"):
                    logger.warning("resume сессии чата %s не удался (%s) — сброс и повтор",
                                   chat_id, type(e).__name__)
                    clear_session(chat_id)
                    continue
                logger.exception("run_collect failed")
                return ERR_RUN

            save_session(chat_id, final_sid)
            if had_error:
                clear_session(chat_id)
                return ERR_SESSION
            return (result_text or "\n\n".join(texts)).strip() or "(пустой ответ)"
