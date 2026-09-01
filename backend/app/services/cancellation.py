from __future__ import annotations

from queue import Empty, Queue
from threading import Thread
from typing import Callable, TypeVar


T = TypeVar("T")


class OperationCanceled(RuntimeError):
    pass


def run_cancelable(
    operation: Callable[[], T],
    should_cancel: Callable[[], bool] | None,
    *,
    poll_seconds: float = 0.1,
) -> T:
    """Wait for a blocking third-party call while polling task cancellation."""
    if should_cancel is None:
        return operation()
    if should_cancel():
        raise OperationCanceled()

    result: Queue[tuple[bool, object]] = Queue(maxsize=1)

    def invoke() -> None:
        try:
            result.put((True, operation()))
        except BaseException as exc:
            result.put((False, exc))

    Thread(target=invoke, name="nicokara-cancelable-call", daemon=True).start()
    while True:
        try:
            succeeded, value = result.get(timeout=poll_seconds)
        except Empty:
            if should_cancel():
                raise OperationCanceled()
            continue
        if succeeded:
            return value  # type: ignore[return-value]
        raise value  # type: ignore[misc]
