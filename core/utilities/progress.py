"""Optional per-execution progress events, independent of ComfyUI and artifacts."""
from contextlib import contextmanager
from contextvars import ContextVar

_REPORTER = ContextVar("vc_director_progress", default=None)


@contextmanager
def progress_scope(reporter):
    token = _REPORTER.set(reporter)
    try:
        yield
    finally:
        _REPORTER.reset(token)


def emit_progress(event, **data):
    reporter = _REPORTER.get()
    if reporter is not None:
        reporter(event, **data)
