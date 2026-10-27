"""Live run #4 (2026-09-27): Windows went to sleep during a 15-minute generation and the run died.
Fix: keep_awake asks Windows (SetThreadExecutionState) not to sleep while a unit is generated and
always hands control back afterwards. Offline: fake kernel32. All tests FAIL before keep_awake exists."""
import pytest

import keep_awake as ka
from test_app_ui import _all_text, _app, _generate, _undo_driver_patches  # noqa: F401 (autouse: undo UI-test patches)


class FakeKernel32:
    def __init__(self, result=1, fail=False):
        self.calls, self.result, self.fail = [], result, fail

    def SetThreadExecutionState(self, flags):
        self.calls.append(flags)
        if self.fail:
            raise OSError("no")
        return self.result


def test_asks_windows_to_stay_awake_then_releases():
    k = FakeKernel32()
    with ka.keep_awake(kernel32=k) as awake:
        assert awake is True
        assert k.calls == [ka.ES_CONTINUOUS | ka.ES_SYSTEM_REQUIRED]
    assert k.calls[-1] == ka.ES_CONTINUOUS                     # back to normal: sleep allowed again


def test_released_even_when_generation_fails():
    k = FakeKernel32()
    with pytest.raises(RuntimeError):
        with ka.keep_awake(kernel32=k):
            raise RuntimeError("Groq 429")
    assert k.calls[-1] == ka.ES_CONTINUOUS


def test_windows_refusing_never_breaks_generation():
    for k in (FakeKernel32(result=0), FakeKernel32(fail=True)):
        with ka.keep_awake(kernel32=k) as awake:
            assert awake is False


def test_not_windows_is_a_quiet_no_op(monkeypatch):
    monkeypatch.setattr(ka.os, "name", "posix")
    with ka.keep_awake() as awake:
        assert awake is False


def test_the_screen_keeps_the_pc_awake_only_while_generating(tmp_path, monkeypatch):
    seen = []

    class Rec:
        def __enter__(self):
            seen.append("on")
            return True

        def __exit__(self, *a):
            seen.append("off")
            return False
    monkeypatch.setattr(ka, "keep_awake", lambda kernel32=None: Rec())
    at, _ = _app(tmp_path)
    assert seen == []                                          # just opening the page: nothing
    _generate(at)
    assert seen == ["on", "off"]
    assert "kept awake" in _all_text(at).lower()
