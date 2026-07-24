import pytest

from app import config, db


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Каждый тест — со свежей БД во временной директории."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "_conn", None)
    yield
    if db._conn is not None:
        db._conn.close()
        db._conn = None


def add(id: int, text: str, *, date: str = "2026-01-01 10:00:00", sender_id: int = 1,
        sender: str = "Алиса", reply_to: int | None = None, topic_id: int | None = None,
        media: str | None = None, filename: str | None = None, reactions: int = 0,
        pinned: bool = False) -> None:
    db.upsert_message(id, date, sender_id, sender, reply_to, topic_id, media,
                      filename, reactions, pinned, text)
