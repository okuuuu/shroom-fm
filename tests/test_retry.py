import requests
import pytest

from shroom_fm.retry import call_with_retry, get_with_retry


def _http_error(status_code: int) -> requests.exceptions.HTTPError:
    response = requests.Response()
    response.status_code = status_code
    return requests.exceptions.HTTPError(response=response)


class _FailNTimes:
    def __init__(self, fail_count, exc_factory, return_value="ok"):
        self.fail_count = fail_count
        self.exc_factory = exc_factory
        self.return_value = return_value
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.fail_count:
            raise self.exc_factory()
        return self.return_value


def test_call_with_retry_returns_immediately_on_success():
    sleeps = []
    target = _FailNTimes(fail_count=0, exc_factory=requests.exceptions.Timeout)

    result = call_with_retry(target, sleep=sleeps.append)

    assert result == "ok"
    assert target.calls == 1
    assert sleeps == []


def test_call_with_retry_retries_transient_failure_then_succeeds():
    sleeps = []
    target = _FailNTimes(fail_count=1, exc_factory=requests.exceptions.ConnectionError)

    result = call_with_retry(target, sleep=sleeps.append)

    assert result == "ok"
    assert target.calls == 2
    assert sleeps == [1.0]


def test_call_with_retry_exhausts_all_attempts_then_raises():
    sleeps = []
    target = _FailNTimes(fail_count=5, exc_factory=requests.exceptions.Timeout)

    with pytest.raises(requests.exceptions.Timeout):
        call_with_retry(target, max_attempts=3, sleep=sleeps.append)

    assert target.calls == 3
    assert sleeps == [1.0, 2.0]


def test_call_with_retry_does_not_retry_service_exception():
    from owslib.util import ServiceException

    sleeps = []
    target = _FailNTimes(fail_count=5, exc_factory=lambda: ServiceException("bad request"))

    with pytest.raises(ServiceException):
        call_with_retry(target, sleep=sleeps.append)

    assert target.calls == 1
    assert sleeps == []


def test_call_with_retry_does_not_retry_client_error_http_status():
    sleeps = []
    target = _FailNTimes(fail_count=5, exc_factory=lambda: _http_error(404))

    with pytest.raises(requests.exceptions.HTTPError):
        call_with_retry(target, sleep=sleeps.append)

    assert target.calls == 1
    assert sleeps == []


def test_call_with_retry_does_retry_server_error_http_status():
    sleeps = []
    target = _FailNTimes(fail_count=1, exc_factory=lambda: _http_error(503))

    result = call_with_retry(target, sleep=sleeps.append)

    assert result == "ok"
    assert target.calls == 2
    assert sleeps == [1.0]


def test_call_with_retry_does_not_retry_plain_value_error():
    sleeps = []
    target = _FailNTimes(fail_count=5, exc_factory=lambda: ValueError("malformed"))

    with pytest.raises(ValueError):
        call_with_retry(target, sleep=sleeps.append)

    assert target.calls == 1
    assert sleeps == []


class _FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _http_error(self.status_code)


def test_get_with_retry_returns_response_on_success(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeResponse(200)

    monkeypatch.setattr("shroom_fm.retry.requests.get", fake_get)

    result = get_with_retry(
        "http://example.com", params={"a": 1}, timeout=30, sleep=lambda s: None
    )

    assert result.status_code == 200
    assert calls == [("http://example.com", {"params": {"a": 1}, "timeout": 30})]


def test_get_with_retry_retries_server_error_then_succeeds(monkeypatch):
    responses = [_FakeResponse(503), _FakeResponse(200)]
    sleeps = []

    def fake_get(url, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr("shroom_fm.retry.requests.get", fake_get)

    result = get_with_retry("http://example.com", sleep=sleeps.append)

    assert result.status_code == 200
    assert sleeps == [1.0]


def test_get_with_retry_does_not_retry_client_error(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _FakeResponse(400)

    monkeypatch.setattr("shroom_fm.retry.requests.get", fake_get)

    with pytest.raises(requests.exceptions.HTTPError):
        get_with_retry("http://example.com", sleep=lambda s: None)

    assert len(calls) == 1
