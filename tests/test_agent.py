from datetime import datetime, timedelta

from app import agent, config


def _write(chat_id: int, sid: str, ago_hours: float) -> None:
    ts = (datetime.now() - timedelta(hours=ago_hours)).isoformat(timespec="seconds")
    config.AGENT_SESSIONS.write_text(
        f'{{"{chat_id}": {{"session_id": "{sid}", "last_used": "{ts}"}}}}')


def test_fresh_session_survives(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AGENT_SESSIONS", tmp_path / "s.json")
    _write(1, "abc", ago_hours=1)
    assert agent.load_session_state(1) == ("abc", False)
    assert agent.session_age(1) == "1ч 0м назад"


def test_idle_session_expires_and_is_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AGENT_SESSIONS", tmp_path / "s.json")
    _write(1, "abc", ago_hours=config.SESSION_FRESH_HOURS + 1)
    assert agent.load_session_state(1) == (None, True)
    assert agent.session_age(1) is None
    assert agent.load_session_state(1) == (None, False)  # запись удалена, второй раз не «протухла»
