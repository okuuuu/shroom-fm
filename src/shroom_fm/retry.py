import time

import requests

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = (1.0, 2.0)


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return True
    if isinstance(exc, requests.exceptions.HTTPError):
        response = exc.response
        return response is not None and response.status_code >= 500
    return False


def call_with_retry(
    func,
    *args,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_seconds: tuple[float, ...] = DEFAULT_BACKOFF_SECONDS,
    sleep=time.sleep,
    **kwargs,
):
    for attempt in range(max_attempts):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            if not _is_retryable(exc) or attempt == max_attempts - 1:
                raise
            delay = backoff_seconds[min(attempt, len(backoff_seconds) - 1)]
            sleep(delay)


def get_with_retry(
    url: str,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_seconds: tuple[float, ...] = DEFAULT_BACKOFF_SECONDS,
    sleep=time.sleep,
    **kwargs,
):
    def _get():
        response = requests.get(url, **kwargs)
        response.raise_for_status()
        return response

    return call_with_retry(
        _get, max_attempts=max_attempts, backoff_seconds=backoff_seconds, sleep=sleep
    )
