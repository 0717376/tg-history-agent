from app import db

from .conftest import add


def test_fts_insert_update_delete():
    add(1, "обсуждаем деплой на проде")
    assert db.search("депло*")["total"] == 1
    add(1, "теперь тут про сборку")  # правка сообщения
    assert db.search("депло*")["total"] == 0
    assert db.search("сборк*")["total"] == 1
    db.delete_message(1)
    assert db.search("сборк*")["total"] == 0


def test_search_snippet_link_and_error():
    db.meta_set("internal_chat_id", "555")
    db.meta_set("is_forum", "1")
    add(10, "важное решение по архитектуре", topic_id=7)
    hit = db.search("архитектур*")["hits"][0]
    assert "«архитектуре»" in hit["text"]
    assert hit["link"] == "https://t.me/c/555/7/10"
    assert "error" in db.search('"незакрытая фраза')  # синтаксическая ошибка FTS — не исключение


def test_search_filters_and_order():
    add(1, "миграция базы", sender_id=1, sender="Алиса", date="2026-01-01 10:00:00", topic_id=5)
    add(2, "миграция сервиса", sender_id=2, sender="Боб", date="2026-02-01 10:00:00", topic_id=6)
    assert db.search("миграция", sender="Боб")["total"] == 1
    assert db.search("миграция", topic=5)["total"] == 1
    assert db.search("миграция", date_from="2026-01-15")["total"] == 1
    assert db.search("миграция", date_to="2026-01-15")["total"] == 1
    ids = [h["id"] for h in db.search("миграция", order="date_desc")["hits"]]
    assert ids == [2, 1]


def test_grep_exact_substring():
    add(1, "ошибка KeyError: 'user_id' в проде")
    assert db.grep("KeyError: 'user")["total"] == 1
    assert db.grep("keyerror")["total"] == 1  # регистронезависимо
    assert db.grep("ValueError")["total"] == 0


def test_context_stays_in_topic():
    for i in range(1, 8):
        add(i, f"сообщение {i}", topic_id=1 if i % 2 else 2)
    ids = [m["id"] for m in db.context(3, before=2, after=2)]
    assert ids == [1, 3, 5, 7]  # только топик 1
    add(100, "вне топика")
    assert [m["id"] for m in db.context(100, 2, 2)] == [100]
    assert "error" in db.context(999)[0]


def test_thread_up_and_down():
    add(1, "корень")
    add(2, "ответ", reply_to=1)
    add(3, "ответ на ответ", reply_to=2)
    add(4, "другая ветка")
    assert [m["id"] for m in db.thread(3)] == [1, 2, 3]
    assert [m["id"] for m in db.thread(1)] == [1, 2, 3]


def test_read_by_ids_and_range():
    for i in (1, 2, 3, 5):
        add(i, f"текст {i}")
    assert [m["id"] for m in db.read(ids=[3, 1])] == [1, 3]
    assert [m["id"] for m in db.read(from_id=2, to_id=5)] == [2, 3, 5]
    assert "error" in db.read()[0]


def test_members_snapshot():
    db.replace_members([(1, "Алиса"), (2, "Боб")])
    assert db.is_member(1) and not db.is_member(3)
    db.replace_members([])  # пустой снимок игнорируется — не выпиливать всех
    assert db.members_count() == 2
    db.replace_members([(3, "Ева")])
    assert db.is_member(3) and not db.is_member(1)


def test_files_pinned_reactions_activity():
    add(1, "спека", media="document", filename="spec_v2.pdf")
    add(2, "решение", pinned=True, reactions=5)
    add(3, "мем", reactions=9, date="2026-01-02 10:00:00")
    assert db.files("spec")[0]["filename"] == "spec_v2.pdf"
    assert [m["id"] for m in db.pinned_msgs()] == [2]
    assert [m["id"] for m in db.top_reacted()] == [3, 2]
    buckets = db.activity()
    assert [(b["bucket"], b["n"]) for b in buckets] == [("2026-01-01", 2), ("2026-01-02", 1)]


def test_stats_and_link_plain():
    db.meta_set("internal_chat_id", "42")
    db.meta_set("is_forum", "0")
    add(1, "привет", topic_id=None)
    s = db.stats()
    assert s["total"] == 1 and not s["is_forum"]
    assert db.link(1, topic_id=9) == "https://t.me/c/42/1"  # не форум — без топика в url


def test_recent_ids():
    for i in (1, 2, 3, 4):
        add(i, f"m{i}")
    assert db.recent_ids(2) == {3, 4}


def test_usage_log_and_summary():
    db.log_usage(7, "Denis", "private", "как работает поиск?", 12.5, True)
    db.log_usage(7, "Denis", "private", "ещё вопрос", 3.0, False)
    db.log_usage(8, "Danila", "group", "про квен", 5.0, True)
    u = db.usage_summary()
    assert u["total"] == 3 and u["errors"] == 1 and u["last_day"] == 3
    assert [(r["name"], r["n"]) for r in u["top"]] == [("Denis", 2), ("Danila", 1)]
