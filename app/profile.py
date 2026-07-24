"""Сводка по проиндексированной группе: объём, даты, авторы, медиа, топики."""

from . import db


def main() -> None:
    s = db.stats()
    if not s["total"]:
        print("База пуста — сначала запустите индексатор: uv run python -m app.indexer")
        return
    print(f"Группа: «{s['title']}»  {'(форум)' if s['is_forum'] else '(плоский чат)'}")
    print(f"Сообщений: {s['total']}  (id {s['min_id']}–{s['max_id']}, "
          f"бэкфилл {'завершён' if s['backfill_done'] else 'ИДЁТ'})")
    print(f"Период: {s['date_from']} — {s['date_to']}\n")
    print("Типы сообщений:")
    for m in s["media"]:
        print(f"  {m['kind']:<12} {m['n']}")
    print("\nТоп авторов:")
    for t in s["top_senders"]:
        print(f"  {t['n']:>6}  {t['sender']}")
    if s["topics"]:
        print("\nТопики:")
        for t in s["topics"]:
            print(f"  {t['n']:>6}  [{t['id']}] {t['title']}  (посл. {t['last'] or '—'})")


if __name__ == "__main__":
    main()
