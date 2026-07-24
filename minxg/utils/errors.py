"""minxg/utils/errors.py — Error classification + retry policy.

 inspired by hermes-agent/error_classifier.py but condensed to the
 three categories that actually matter for AgentHarness:

  TRANSIENT   — network hiccup, 502/503, timeout.  Retry with backoff.
  AUTH        — 401/403, bad API key.  Surface immediately, no retry.
  FATAL       — programmer error, disk full, bad config.  Surface immediately.

Anything not explicitly classified falls into TRANSIENT by default because
the most common production surprise is "the network flaked", not "the code
is wrong".
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Optional, Tuple, Type

logger = logging.getLogger(__name__)


class ErrorClass(Enum):
    TRANSIENT = "transient"
    AUTH = "auth"
    FATAL = "fatal"


class AgentHarnessError(Exception):
    """Base for classified AgentHarness errors."""

    def __init__(self, cls: ErrorClass, message: str = "", cause: Optional[BaseException] = None):
        super().__init__(message)
        self.cls = cls
        self.cause = cause


class TransientError(AgentHarnessError):
    def __init__(self, message: str = "", cause: Optional[BaseException] = None):
        super().__init__(ErrorClass.TRANSIENT, message, cause)


class AuthError(AgentHarnessError):
    def __init__(self, message: str = "", cause: Optional[BaseException] = None):
        super().__init__(ErrorClass.AUTH, message, cause)


class FatalError(AgentHarnessError):
    def __init__(self, message: str = "", cause: Optional[BaseException] = None):
        super().__init__(ErrorClass.FATAL, message, cause)


# HTTP status code → class
_HTTP_CLASS: Dict[int, ErrorClass] = {
    401: ErrorClass.AUTH,
    403: ErrorClass.AUTH,
    408: ErrorClass.TRANSIENT,
    429: ErrorClass.TRANSIENT,
    500: ErrorClass.TRANSIENT,
    502: ErrorClass.TRANSIENT,
    503: ErrorClass.TRANSIENT,
    504: ErrorClass.TRANSIENT,
}

# Exception type → class (checked before HTTP heuristics)
_EXC_CLASS: Dict[Type[BaseException], ErrorClass] = {
    TimeoutError: ErrorClass.TRANSIENT,
    ConnectionError: ErrorClass.TRANSIENT,
    OSError: ErrorClass.TRANSIENT,
    PermissionError: ErrorClass.FATAL,
    FileNotFoundError: ErrorClass.FATAL,
    ValueError: ErrorClass.FATAL,
    TypeError: ErrorClass.FATAL,
    MemoryError: ErrorClass.FATAL,
    RecursionError: ErrorClass.FATAL,
}


def classify(exc: BaseException, status: Optional[int] = None) -> ErrorClass:
    """Classify an exception into TRANSIENT / AUTH / FATAL."""
    if status is not None and status in _HTTP_CLASS:
        return _HTTP_CLASS[status]
    for exc_type, cls in _EXC_CLASS.items():
        if isinstance(exc, exc_type):
            return cls
    # urllib / aiohttp HTTP errors often wrap status in .status / .code
    for attr in ("status", "code", "status_code"):
        val = getattr(exc, attr, None)
        if isinstance(val, int) and val in _HTTP_CLASS:
            return _HTTP_CLASS[val]
    # Default: network is more likely to be transient than code is wrong.
    return ErrorClass.TRANSIENT


@dataclass
class RetryPolicy:
    max_attempts: int = 4
    base_delay: float = 0.5
    max_delay: float = 8.0
    jitter: float = 0.25
    retryable: Tuple[ErrorClass, ...] = (ErrorClass.TRANSIENT,)

    def delay(self, attempt: int) -> float:
        import math
        exp = min(self.base_delay * math.pow(2, attempt), self.max_delay)
        return exp + (self.jitter * (2 * time.time() % 1.0))


def retry(
    fn: Callable[..., Any],
    *args: Any,
    policy: RetryPolicy = RetryPolicy(),
    classify_fn: Callable[[BaseException, Optional[int]], ErrorClass] = classify,
    status_arg: Optional[str] = None,
    **kwargs: Any,
) -> Any:
    """Call `fn(*args, **kwargs)` up to `policy.max_attempts` times.

    If `status_arg` is provided, the call result is inspected as
    ``(value, status)`` and the status is passed to `classify_fn`.
    """
    last: Optional[BaseException] = None
    for attempt in range(policy.max_attempts):
        try:
            result = fn(*args, **kwargs)
            if status_arg and isinstance(result, tuple) and len(result) == 2:
                value, status = result
            else:
                value, status = result, None
            return value
        except BaseException as exc:
            cls = classify_fn(exc, status if status_arg else None)
            last = exc
            if cls not in policy.retryable or attempt + 1 >= policy.max_attempts:
                logger.warning(
                    "retry exhausted after %d/%d %s: %s",
                    attempt + 1, policy.max_attempts, cls.value, exc,
                    exc_info=True,
                )
                raise
            delay = policy.delay(attempt)
            logger.debug(
                "retry %d/%d in %.2fs (%s): %s",
                attempt + 1, policy.max_attempts, delay, cls.value, exc,
            )
            time.sleep(delay)
    raise last  # unreachable; satisfies type checker
