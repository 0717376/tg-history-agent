"""SQLite-хранилище: messages + FTS5, topics, meta. Единственная точка доступа к базе."""

import sqlite3

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY,          -- telegram message id
  date TEXT NOT NULL,              -- ISO UTC
  sender_id INTEGER,
  sender TEXT,
  reply_to INTEGER,
  topic_id INTEGER,
  media TEXT,                      -- photo|document|video|voice|... или NULL
  filename TEXT,                   -- имя файла документа, если есть
  reactions INTEGER DEFAULT 0,     -- суммарное число реакций
  pinned INTEGER DEFAULT 0,
  text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_date ON messages(date);
CREATE INDEX IF NOT EXISTS idx_messages_topic ON messages(topic_id);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  text, content='messages', content_rowid='id',
  tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, text) VALUES('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE OF text ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, text) VALUES('delete', old.id, old.text);
  INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TABLE IF NOT EXISTS topics(
  id INTEGER PRIMARY KEY,          -- id корневого сообщения топика
  title TEXT
);
CREATE TABLE IF NOT EXISTS members(user_id INTEGER PRIMARY KEY, name TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""

_conn: sqlite3.Connection | None = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
    return _conn


# --- meta ---

def meta_get(key: str, default: str | None = None) -> str | None:
    row = conn().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(key: str, value: str) -> None:
    conn().execute("INSERT INTO meta(key,value) VALUES(?,?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    conn().commit()


# --- messages ---

def upsert_message(id: int, date: str, sender_id: int | None, sender: str | None,
                   reply_to: int | None, topic_id: int | None, media: str | None,
                   filename: str | None, reactions: int, pinned: bool,
                   text: str, commit: bool = True) -> None:
    conn().execute(
        """INSERT INTO messages(id,date,sender_id,sender,reply_to,topic_id,media,
                                filename,reactions,pinned,text)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET date=excluded.date, sender_id=excluded.sender_id,
             sender=excluded.sender, reply_to=excluded.reply_to, topic_id=excluded.topic_id,
             media=excluded.media, filename=excluded.filename, reactions=excluded.reactions,
             pinned=excluded.pinned, text=excluded.text""",
        (id, date, sender_id, sender, reply_to, topic_id, media, filename, reactions, int(pinned), text))
    if commit:
        conn().commit()


def delete_message(id: int) -> None:
    conn().execute("DELETE FROM messages WHERE id=?", (id,))
    conn().commit()


def commit() -> None:
    conn().commit()


def upsert_topic(id: int, title: str) -> None:
    conn().execute("INSERT INTO topics(id,title) VALUES(?,?) "
                   "ON CONFLICT(id) DO UPDATE SET title=excluded.title", (id, title))
    conn().commit()


def bounds() -> tuple[int | None, int | None, int]:
    """(min_id, max_id, count)"""
    row = conn().execute("SELECT MIN(id) a, MAX(id) b, COUNT(*) c FROM messages").fetchone()
    return row["a"], row["b"], row["c"]


def replace_members(pairs: list[tuple[int, str]]) -> None:
    """Полная замена снимка участников (пустой список игнорируем — не выпиливать всех)."""
    if not pairs:
        return
    c = conn()
    from datetime import datetime
    now = datetime.now().isoformat(timespec="seconds")
    c.execute("DELETE FROM members")
    c.executemany("INSERT INTO members(user_id,name,updated) VALUES(?,?,?)",
                  [(uid, name, now) for uid, name in pairs])
    c.commit()


def is_member(user_id: int) -> bool:
    return conn().execute("SELECT 1 FROM members WHERE user_id=?", (user_id,)).fetchone() is not None


def members_count() -> int:
    return conn().execute("SELECT COUNT(*) c FROM members").fetchone()["c"]


# --- ссылки ---

def link(msg_id: int, topic_id: int | None = None) -> str:
    internal = meta_get("internal_chat_id", "0")
    if topic_id and meta_get("is_forum") == "1":
        return f"https://t.me/c/{internal}/{topic_id}/{msg_id}"
    return f"https://t.me/c/{internal}/{msg_id}"


# --- поисковый движок (используется тулами агента) ---

def _topic_titles() -> dict[int, str]:
    return {r["id"]: r["title"] for r in conn().execute("SELECT id,title FROM topics")}


def _trunc(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + "…"


def _fmt(row: sqlite3.Row, text: str, topics: dict[int, str]) -> dict:
    d = {
        "id": row["id"], "date": row["date"], "sender": row["sender"],
        "topic": topics.get(row["topic_id"]), "text": text,
        "link": link(row["id"], row["topic_id"]),
    }
    if row["reply_to"]:
        d["reply_to"] = row["reply_to"]
    if row["media"]:
        d["media"] = row["media"]
    if row["filename"]:
        d["filename"] = row["filename"]
    if row["reactions"]:
        d["reactions"] = row["reactions"]
    if row["pinned"]:
        d["pinned"] = True
    return d


def _filters(topic, sender, date_from, date_to, alias="m"):
    """(sql, params) — общие фильтры поиска."""
    sql, params = "", []
    if topic is not None:
        sql += f" AND {alias}.topic_id=?"
        params.append(topic)
    if sender:
        sql += f" AND {alias}.sender LIKE ?"
        params.append(f"%{sender}%")
    if date_from:
        sql += f" AND {alias}.date >= ?"
        params.append(date_from)
    if date_to:
        sql += f" AND {alias}.date <= ?"
        params.append(date_to + (" 23:59:59" if len(date_to) == 10 else ""))
    return sql, params


ORDERS = {"relevance": "rank", "date_desc": "m.date DESC", "date_asc": "m.date ASC"}


def search(query: str, topic=None, sender=None, date_from=None, date_to=None,
           order: str = "relevance", limit: int = 20, offset: int = 0) -> dict:
    fsql, fparams = _filters(topic, sender, date_from, date_to)
    sql = f"""SELECT m.*, snippet(messages_fts, 0, '«', '»', ' … ', 15) snip
              FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid
              WHERE messages_fts MATCH ?{fsql}
              ORDER BY {ORDERS.get(order, 'rank')} LIMIT ? OFFSET ?"""
    try:
        rows = conn().execute(sql, [query, *fparams, limit, offset]).fetchall()
    except sqlite3.OperationalError as e:
        return {"error": f"Ошибка FTS-запроса: {e}. Синтаксис: слова (И), OR, \"фраза\", префикс*."}
    topics = _topic_titles()
    total = None
    try:
        total = conn().execute(
            f"SELECT COUNT(*) c FROM messages_fts JOIN messages m ON m.id=messages_fts.rowid "
            f"WHERE messages_fts MATCH ?{fsql}", [query, *fparams]).fetchone()["c"]
    except sqlite3.OperationalError:
        pass
    return {"total": total, "offset": offset,
            "hits": [_fmt(r, r["snip"], topics) for r in rows]}


def grep(substring: str, topic=None, sender=None, date_from=None, date_to=None,
         limit: int = 20, offset: int = 0) -> dict:
    fsql, fparams = _filters(topic, sender, date_from, date_to)
    where = f"instr(lower(m.text), lower(?)) > 0{fsql}"
    total = conn().execute(f"SELECT COUNT(*) c FROM messages m WHERE {where}",
                           [substring, *fparams]).fetchone()["c"]
    rows = conn().execute(
        f"SELECT m.* FROM messages m WHERE {where} ORDER BY m.date DESC LIMIT ? OFFSET ?",
        [substring, *fparams, limit, offset]).fetchall()
    topics = _topic_titles()
    return {"total": total, "offset": offset,
            "hits": [_fmt(r, _trunc(r["text"], 400), topics) for r in rows]}


def read(ids: list[int] | None = None, from_id: int | None = None,
         to_id: int | None = None, limit: int = 100) -> list[dict]:
    if ids:
        marks = ",".join("?" * len(ids))
        rows = conn().execute(
            f"SELECT * FROM messages WHERE id IN ({marks}) ORDER BY id LIMIT ?",
            [*ids, limit]).fetchall()
    elif from_id is not None and to_id is not None:
        rows = conn().execute(
            "SELECT * FROM messages WHERE id BETWEEN ? AND ? ORDER BY id LIMIT ?",
            (from_id, to_id, limit)).fetchall()
    else:
        return [{"error": "нужны ids или from_id+to_id"}]
    topics = _topic_titles()
    return [_fmt(r, _trunc(r["text"], 1500), topics) for r in rows]


def context(message_id: int, before: int = 10, after: int = 10) -> list[dict]:
    row = conn().execute("SELECT topic_id FROM messages WHERE id=?", (message_id,)).fetchone()
    if not row:
        return [{"error": f"сообщение {message_id} не в индексе"}]
    tid = row["topic_id"]
    tcond, tparams = ("topic_id=?", [tid]) if tid is not None else ("topic_id IS NULL", [])
    prev = conn().execute(
        f"SELECT * FROM messages WHERE {tcond} AND id < ? ORDER BY id DESC LIMIT ?",
        [*tparams, message_id, before]).fetchall()
    rest = conn().execute(
        f"SELECT * FROM messages WHERE {tcond} AND id >= ? ORDER BY id LIMIT ?",
        [*tparams, message_id, after + 1]).fetchall()
    topics = _topic_titles()
    return [_fmt(r, _trunc(r["text"], 800), topics) for r in [*reversed(prev), *rest]]


def thread(message_id: int) -> list[dict]:
    c = conn()
    root_id = message_id
    for _ in range(50):  # вверх до корня
        row = c.execute("SELECT reply_to FROM messages WHERE id=?", (root_id,)).fetchone()
        if not row or not row["reply_to"]:
            break
        parent = c.execute("SELECT id FROM messages WHERE id=?", (row["reply_to"],)).fetchone()
        if not parent:
            break
        root_id = parent["id"]
    ids, frontier = {root_id}, [root_id]
    while frontier and len(ids) < 200:  # вниз по всем ответам
        marks = ",".join("?" * len(frontier))
        rows = c.execute(f"SELECT id FROM messages WHERE reply_to IN ({marks})", frontier).fetchall()
        frontier = [r["id"] for r in rows if r["id"] not in ids]
        ids.update(frontier)
    marks = ",".join("?" * len(ids))
    rows = c.execute(f"SELECT * FROM messages WHERE id IN ({marks}) ORDER BY id", list(ids)).fetchall()
    topics = _topic_titles()
    return [_fmt(r, _trunc(r["text"], 800), topics) for r in rows]


def senders(query: str | None = None) -> list[dict]:
    sql = """SELECT sender, sender_id, COUNT(*) n, MIN(date) first, MAX(date) last
             FROM messages GROUP BY sender_id"""
    params: list = []
    if query:
        sql += " HAVING sender LIKE ?"
        params.append(f"%{query}%")
    sql += " ORDER BY n DESC"
    return [dict(r) for r in conn().execute(sql, params)]


def pinned_msgs() -> list[dict]:
    rows = conn().execute("SELECT * FROM messages WHERE pinned=1 ORDER BY id").fetchall()
    topics = _topic_titles()
    return [_fmt(r, _trunc(r["text"], 800), topics) for r in rows]


def files(query: str | None = None, limit: int = 30) -> list[dict]:
    sql, params = "SELECT * FROM messages WHERE filename IS NOT NULL", []
    if query:
        sql += " AND (filename LIKE ? OR text LIKE ?)"
        params += [f"%{query}%", f"%{query}%"]
    sql += " ORDER BY date DESC LIMIT ?"
    rows = conn().execute(sql, [*params, limit]).fetchall()
    topics = _topic_titles()
    return [_fmt(r, _trunc(r["text"], 300), topics) for r in rows]


def top_reacted(topic=None, date_from=None, date_to=None, limit: int = 15) -> list[dict]:
    fsql, fparams = _filters(topic, None, date_from, date_to)
    rows = conn().execute(
        f"SELECT m.* FROM messages m WHERE m.reactions > 0{fsql} "
        f"ORDER BY m.reactions DESC LIMIT ?", [*fparams, limit]).fetchall()
    topics = _topic_titles()
    return [_fmt(r, _trunc(r["text"], 500), topics) for r in rows]


def activity(date_from=None, date_to=None, topic=None, granularity: str = "day") -> list[dict]:
    fmt = "%Y-%W" if granularity == "week" else "%Y-%m-%d"
    fsql, fparams = _filters(topic, None, date_from, date_to)
    return [dict(r) for r in conn().execute(
        f"""SELECT strftime('{fmt}', m.date) bucket, COUNT(*) n,
                   MIN(m.id) first_id, MAX(m.id) last_id
            FROM messages m WHERE 1=1{fsql} GROUP BY bucket ORDER BY bucket""", fparams)]


# --- профиль/статистика ---

def stats() -> dict:
    c = conn()
    mn, mx, total = bounds()
    dates = c.execute("SELECT MIN(date) a, MAX(date) b FROM messages").fetchone()
    top = [dict(r) for r in c.execute(
        "SELECT sender, COUNT(*) n FROM messages GROUP BY sender_id ORDER BY n DESC LIMIT 15")]
    media = [dict(r) for r in c.execute(
        "SELECT COALESCE(media,'text') kind, COUNT(*) n FROM messages GROUP BY 1 ORDER BY n DESC")]
    topics = [dict(r) for r in c.execute(
        """SELECT t.id, t.title, COUNT(m.id) n, MAX(m.date) last
           FROM topics t LEFT JOIN messages m ON m.topic_id=t.id
           GROUP BY t.id ORDER BY n DESC""")]
    return {
        "title": meta_get("title"), "is_forum": meta_get("is_forum") == "1",
        "backfill_done": meta_get("backfill_done") == "1",
        "total": total, "min_id": mn, "max_id": mx,
        "date_from": dates["a"], "date_to": dates["b"],
        "top_senders": top, "media": media, "topics": topics,
    }
