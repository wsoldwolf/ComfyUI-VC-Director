"""Per-Build cache policy, isolated from other queued executions."""
from contextlib import contextmanager
from contextvars import ContextVar

from .cache import SuccessCache
from ..errors import require
from ..utilities import canonical, digest

MODES = {"再利用": "reuse", "再生成": "refresh", "無効": "disabled",
         "reuse": "reuse", "refresh": "refresh", "disabled": "disabled"}
_POLICY = ContextVar("vcd_build_cache_policy", default=("reuse", None))


def cache_mode(value):
    require(value in MODES, "CACHE_MODE", "cache_mode", "キャッシュ設定は再利用・再生成・無効から選択してください。")
    return MODES[value]


@contextmanager
def cache_scope(mode, signature=None):
    token = _POLICY.set((cache_mode(mode), signature))
    try:
        yield
    finally:
        _POLICY.reset(token)


class PipelineCache(SuccessCache):
    """Reuse the atomic success store; do not delete another execution's data."""
    @staticmethod
    def scoped_key(key):
        signature = _POLICY.get()[1]
        return digest(canonical([signature, key])) if signature is not None else key

    def get(self, key):
        if _POLICY.get()[0] != "reuse":
            return None
        return super().get(self.scoped_key(key))

    def put_success(self, key, payload):
        if _POLICY.get()[0] != "disabled":
            super().put_success(self.scoped_key(key), payload)
