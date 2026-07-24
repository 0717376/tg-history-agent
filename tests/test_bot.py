from app.bot import md_to_tg_html, split_md


def test_md_links_and_bold():
    html = md_to_tg_html("**важно**: [сообщение](https://t.me/c/1/2) и `код`")
    assert '<a href="https://t.me/c/1/2">сообщение</a>' in html
    assert "<b>важно</b>" in html
    assert "<code>код</code>" in html


def test_md_escapes_html():
    assert md_to_tg_html("a < b & c") == "a &lt; b &amp; c"


def test_md_code_block_preserved():
    html = md_to_tg_html("```py\nx = 1 < 2\n```")
    assert "<pre>x = 1 &lt; 2\n</pre>" in html


def test_md_headings_and_bullets():
    html = md_to_tg_html("# Заголовок\n- пункт")
    assert "<b>Заголовок</b>" in html
    assert "• пункт" in html


def test_split_md_respects_limit():
    text = "\n".join(f"строка {i}" for i in range(1000))
    chunks = split_md(text, limit=500)
    assert all(len(c) <= 500 for c in chunks)
    assert "\n".join(chunks) == text
