from signals import telegram


def test_chunk_splits_on_newlines_under_the_limit():
    text = "line one\nline two\nline three"
    assert telegram._chunk(text, max_len=100) == [text]


def test_chunk_splits_into_multiple_messages_when_lines_dont_fit():
    text = "a" * 40 + "\n" + "b" * 40 + "\n" + "c" * 40
    chunks = telegram._chunk(text, max_len=50)
    assert chunks == ["a" * 40, "b" * 40, "c" * 40]
    assert all(len(c) <= 50 for c in chunks)


def test_chunk_hard_splits_a_single_line_longer_than_max_len():
    # A single line (no newlines) longer than max_len used to pass through
    # as its own oversized chunk - Telegram then rejected it outright with
    # "message is too long" rather than truncating it, which crashed the
    # whole run (send_message raises on a non-2xx response). No chunk
    # should ever exceed max_len, regardless of input shape.
    text = "x" * 250
    chunks = telegram._chunk(text, max_len=100)
    assert all(len(c) <= 100 for c in chunks)
    assert "".join(chunks) == text


def test_chunk_hard_split_mixes_with_normal_lines():
    text = "short line\n" + ("y" * 220) + "\nanother short line"
    chunks = telegram._chunk(text, max_len=100)
    assert all(len(c) <= 100 for c in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")
