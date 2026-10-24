from signals import universe

_CSV = "Company Name,Industry,Symbol,Series,ISIN Code\n" + "\n".join(
    f"Company {i},{'Capital Goods' if i % 2 else 'Financial Services'},SYM{i},EQ,INE{i:07d}" for i in range(450)
)


class _FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class _FakeSession:
    def get(self, url, headers=None, timeout=None):
        return _FakeResponse(_CSV)


def test_constituents_carry_each_symbols_industry(monkeypatch):
    monkeypatch.setattr(universe.requests, "Session", _FakeSession)
    constituents = universe.fetch_nifty500_constituents()
    assert len(constituents) == 450
    assert constituents["SYM1"] == "Capital Goods"
    assert constituents["SYM2"] == "Financial Services"


def test_symbols_are_the_sorted_constituent_keys(monkeypatch):
    monkeypatch.setattr(universe.requests, "Session", _FakeSession)
    assert universe.fetch_nifty500_symbols() == sorted(f"SYM{i}" for i in range(450))
