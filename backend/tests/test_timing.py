import builtins

from app.core import timing


def test_emit_timing_never_raises(monkeypatch):
    def broken_print(*args, **kwargs):
        raise OSError(233, "管道的另一端上无任何进程。")

    monkeypatch.setattr(builtins, "print", broken_print)

    timing.emit_timing("demo_event", value=1)
