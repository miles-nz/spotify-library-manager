from __future__ import annotations

from typing import Callable

CancelCheck = Callable[[], bool]


class OperationCancelled(Exception):
    """Raised when a cancel_check callback reports a cancellation request."""


def check_cancelled(cancel_check: CancelCheck | None) -> None:
    if cancel_check and cancel_check():
        raise OperationCancelled()
