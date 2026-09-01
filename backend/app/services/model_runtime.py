from __future__ import annotations

import gc
import sys
import threading
from datetime import UTC, datetime
from typing import Any, Callable


class ResidentModelStore:
    """Keep one heavy model resident and release it when another is requested."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._key: str | None = None
        self._label: str | None = None
        self._value: Any = None
        self._loaded_at: str | None = None

    def get_or_load(self, key: str, label: str, loader: Callable[[], Any]) -> Any:
        with self._lock:
            if self._key == key and self._value is not None:
                return self._value
            self._release_locked()
            value = loader()
            self._key = key
            self._label = label
            self._value = value
            self._loaded_at = datetime.now(UTC).isoformat()
            return value

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "loaded": self._value is not None,
                "key": self._key,
                "label": self._label,
                "loaded_at": self._loaded_at,
            }

    def release(self) -> dict[str, Any]:
        with self._lock:
            released = self._value is not None
            label = self._label
            self._release_locked()
            return {**self.status(), "released": released, "released_label": label}

    def _release_locked(self) -> None:
        self._value = None
        self._key = None
        self._label = None
        self._loaded_at = None
        gc.collect()
        torch = sys.modules.get("torch")
        cuda = getattr(torch, "cuda", None)
        try:
            if cuda is not None and callable(getattr(cuda, "is_available", None)) and cuda.is_available():
                cuda.empty_cache()
        except Exception:
            # Releasing CPU/DirectML references still succeeds if CUDA probing fails.
            pass
