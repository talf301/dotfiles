import json

from symphony.notify import MultiNotifier, NtfyNotifier


def test_ntfy_sends_only_id_reason_and_link_by_default():
    sent = []
    n = NtfyNotifier({"topic": "t-123", "issue_url": "https://linear.app/ws/issue/"},
                     post=lambda url, data, headers: sent.append((url, data, headers)))
    n.notify("TAL-179: In Review", "Secret ticket title (symphony open TAL-179)")
    url, data, headers = sent[0]
    assert url == "https://ntfy.sh/t-123"
    assert data == b"TAL-179: In Review" and b"Secret" not in data
    assert headers["Click"] == "https://linear.app/ws/issue/TAL-179"


def test_ntfy_include_detail_and_failures_never_raise():
    sent = []
    n = NtfyNotifier({"topic": "t", "include_detail": True},
                     post=lambda url, data, headers: sent.append(data))
    n.notify("TAL-1: Done", "detail")
    assert sent == [b"detail"]

    def boom(*a):
        raise OSError("offline")
    NtfyNotifier({"topic": "t"}, post=boom).notify("TAL-1: Done", "x")  # no exception


def test_from_file_missing_or_invalid_disables_ntfy(tmp_path, monkeypatch):
    monkeypatch.setattr("symphony.notify.log", lambda *a, **k: None)
    assert NtfyNotifier.from_file(tmp_path / "none.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{")
    assert NtfyNotifier.from_file(bad) is None
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"topic": "abc"}))
    assert NtfyNotifier.from_file(good).topic == "abc"


def test_multi_skips_missing_backends():
    got = []

    class N:
        def notify(self, t, m):
            got.append(t)
    MultiNotifier(N(), None, N()).notify("TAL-2: Done", "x")
    assert got == ["TAL-2: Done", "TAL-2: Done"]
