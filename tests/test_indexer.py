from types import SimpleNamespace

from app.indexer import _media_kind, _reply_to, _topic_id


def _msg(reply_to=None, **extra):
    base = {k: None for k in ("photo", "sticker", "voice", "video_note", "video",
                              "audio", "document", "poll", "contact", "geo")}
    return SimpleNamespace(reply_to=reply_to, **{**base, **extra})


def test_forum_topic_header_is_not_a_reply():
    # обычное сообщение в топике: reply-заголовок указывает на корень топика
    header = SimpleNamespace(forum_topic=True, reply_to_msg_id=22, reply_to_top_id=None)
    m = _msg(reply_to=header)
    assert _reply_to(m) is None
    assert _topic_id(m) == 22


def test_real_reply_inside_topic():
    rt = SimpleNamespace(forum_topic=True, reply_to_msg_id=105, reply_to_top_id=22)
    m = _msg(reply_to=rt)
    assert _reply_to(m) == 105
    assert _topic_id(m) == 22


def test_plain_chat_reply():
    rt = SimpleNamespace(forum_topic=False, reply_to_msg_id=7, reply_to_top_id=None)
    m = _msg(reply_to=rt)
    assert _reply_to(m) == 7
    assert _topic_id(m) is None
    assert _reply_to(_msg()) is None


def test_media_kind_priority():
    assert _media_kind(_msg(photo=object())) == "photo"
    assert _media_kind(_msg(document=object())) == "document"
    assert _media_kind(_msg()) is None
