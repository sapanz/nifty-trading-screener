import pytest

from signals import universe


def _csv(n=450):
    lines = ["Company Name,Industry,Symbol,Series,ISIN Code"]
    for i in range(n):
        lines.append(f"Company {i},{'Healthcare' if i % 3 == 0 else 'Financial Services'},SYM{i:03d},EQ,INE{i:09d}")
    lines.append("No Industry Ltd,,NOIND,EQ,INE000000000")
    return "\n".join(lines)


class _Resp:
    def __init__(self, text, ok=True):
        self.text, self.ok = text, ok

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError("HTTP 403")


class _Session:
    """Answers every request (homepage warm-up included) with the same CSV."""

    def __init__(self, text):
        self.text = text

    def get(self, url, **kwargs):
        return _Resp(self.text)


@pytest.fixture
def patched(monkeypatch):
    def install(text):
        monkeypatch.setattr(universe.requests, "Session", lambda: _Session(text))
    return install


def test_symbols_and_sector_map_come_from_the_same_file(patched):
    patched(_csv())
    symbols = universe.fetch_nifty500_symbols()
    sectors = universe.fetch_sector_map()
    assert len(symbols) == 451 and symbols == sorted(symbols)
    assert sectors["SYM000"] == "Healthcare" and sectors["SYM001"] == "Financial Services"
    assert "NOIND" not in sectors                       # a row without an industry is left out, not mapped to ""


def test_an_implausibly_short_list_is_rejected(patched):
    patched(_csv(n=50))
    with pytest.raises(RuntimeError, match="Could not fetch"):
        universe.fetch_sector_map()
