import json

import pytest

from signals import secure


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(secure.ENV_VAR, raising=False)


def test_round_trip_and_envelope_shape():
    obj = {"a": [1, 2, 3], "name": "ऋ ₹", "n": None}
    env = secure.encrypt(obj, "correct horse")
    assert secure.is_encrypted(env) and env["alg"] == "AES-256-GCM" and env["iter"] == secure.ITERATIONS
    assert "correct horse" not in json.dumps(env) and "ऋ" not in json.dumps(env)
    assert secure.decrypt(env, "correct horse") == obj


def test_wrong_password_and_tampering_fail():
    env = secure.encrypt({"x": 1}, "pw1")
    with pytest.raises(Exception):
        secure.decrypt(env, "pw2")
    bad = dict(env, ct=env["ct"][:-4] + ("AAAA" if not env["ct"].endswith("AAAA") else "BBBB"))
    with pytest.raises(Exception):
        secure.decrypt(bad, "pw1")


def test_fresh_iv_per_file():
    assert secure.encrypt({"x": 1}, "pw")["iv"] != secure.encrypt({"x": 1}, "pw")["iv"]


def test_plain_when_no_password_and_loads_handles_both(monkeypatch):
    assert json.loads(secure.dumps({"x": 1})) == {"x": 1}
    assert secure.loads('{"x":1}') == {"x": 1}
    monkeypatch.setenv(secure.ENV_VAR, "pw")
    text = secure.dumps({"x": 1})
    assert secure.is_encrypted(json.loads(text)) and secure.loads(text) == {"x": 1}
    monkeypatch.delenv(secure.ENV_VAR)
    with pytest.raises(RuntimeError):
        secure.loads(text)


def test_write_json(tmp_path, monkeypatch):
    monkeypatch.setenv(secure.ENV_VAR, "pw")
    secure.write_json(tmp_path / "f.json", {"k": list(range(50))})
    assert secure.loads((tmp_path / "f.json").read_text()) == {"k": list(range(50))}
