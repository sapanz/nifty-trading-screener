from datetime import date

import pytest

from signals import events


class _FakeResponse:
    def __init__(self, payload=None, status_code: int = 200, text: str = ""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _FakeSession:
    def __init__(self, api_response: _FakeResponse):
        self.headers: dict = {}
        self.api_response = api_response
        self.calls: list[tuple] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        if url == "https://www.nseindia.com":
            return _FakeResponse({})
        return self.api_response


def _patch(monkeypatch, response: _FakeResponse) -> _FakeSession:
    session = _FakeSession(response)
    monkeypatch.setattr(events.requests, "Session", lambda: session)
    return session


def test_returns_symbols_from_calendar_rows(monkeypatch):
    _patch(monkeypatch, _FakeResponse([
        {"symbol": "TCS", "purpose": "Financial Results", "date": "20-Oct-2026"},
        {"symbol": "INFY ", "purpose": "Dividend", "date": "21-Oct-2026"},
        {"symbol": "TCS", "purpose": "Board Meeting", "date": "21-Oct-2026"},
    ]))
    assert events.fetch_event_symbols(date(2026, 10, 19)) == {"TCS", "INFY"}


def test_queries_the_window_around_the_date(monkeypatch):
    session = _patch(monkeypatch, _FakeResponse([]))
    events.fetch_event_symbols(date(2026, 10, 19), lookback_days=2, lookahead_days=4)
    url, params = session.calls[-1]
    assert url == events.config.NSE_EVENT_CALENDAR_URL
    assert params == {"index": "equities", "from_date": "17-10-2026", "to_date": "23-10-2026"}


def test_empty_calendar_returns_empty_set(monkeypatch):
    _patch(monkeypatch, _FakeResponse([]))
    assert events.fetch_event_symbols(date(2026, 10, 19)) == set()


def test_raises_on_http_error(monkeypatch):
    _patch(monkeypatch, _FakeResponse([], status_code=403))
    with pytest.raises(RuntimeError):
        events.fetch_event_symbols(date(2026, 10, 19))


def test_raises_on_non_json(monkeypatch):
    _patch(monkeypatch, _FakeResponse(None, text="<html>Access Denied</html>"))
    with pytest.raises(RuntimeError, match="non-JSON"):
        events.fetch_event_symbols(date(2026, 10, 19))


def test_raises_on_unexpected_shape(monkeypatch):
    _patch(monkeypatch, _FakeResponse({"data": []}))
    with pytest.raises(RuntimeError, match="unexpected shape"):
        events.fetch_event_symbols(date(2026, 10, 19))


def test_raises_when_rows_lack_symbol(monkeypatch):
    _patch(monkeypatch, _FakeResponse([{"company": "Tata Consultancy"}]))
    with pytest.raises(RuntimeError, match="no 'symbol' field"):
        events.fetch_event_symbols(date(2026, 10, 19))
