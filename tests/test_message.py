"""Push validates what it can before any route or secret is in play."""

import pytest

from pushpush import InvalidPushError, MediaError, Push


def test_text_only_push_is_valid():
    push = Push(text="hello")
    assert push.text == "hello"
    assert push.media is None


def test_media_only_push_is_valid(tmp_path):
    chart = tmp_path / "chart.png"
    chart.write_bytes(b"\x89PNG")
    push = Push(media=chart)
    assert push.media == chart
    assert push.text is None


def test_empty_push_is_refused():
    with pytest.raises(InvalidPushError):
        Push()


def test_empty_text_is_refused():
    with pytest.raises(InvalidPushError):
        Push(text="")


def test_whitespace_only_text_is_refused():
    with pytest.raises(InvalidPushError):
        Push(text="   \n\t")


def test_empty_push_is_also_a_value_error():
    # InvalidPushError inherits ValueError, so both except clauses catch it.
    with pytest.raises(ValueError):
        Push()


def test_caption_without_media_is_refused():
    with pytest.raises(InvalidPushError):
        Push(text="body", caption="label")


def test_missing_media_path_is_refused(tmp_path):
    with pytest.raises(MediaError):
        Push(media=tmp_path / "nope.png")


def test_directory_as_media_is_refused(tmp_path):
    with pytest.raises(MediaError):
        Push(media=tmp_path)


def test_caption_or_text_prefers_caption(tmp_path):
    chart = tmp_path / "c.png"
    chart.write_bytes(b"x")
    push = Push(media=chart, caption="from caption", text="from text")
    assert push.caption_or_text == "from caption"


def test_caption_or_text_falls_back_to_text(tmp_path):
    chart = tmp_path / "c.png"
    chart.write_bytes(b"x")
    push = Push(media=chart, text="from text")
    assert push.caption_or_text == "from text"


def test_push_is_frozen():
    push = Push(text="hi")
    with pytest.raises(AttributeError):
        push.text = "bye"  # type: ignore[misc]
