import ssl
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http import HTTPStatus

import httpx

from game_automation.common import FrameworkError
from game_automation.errors import ErrorCode

MAX_REDIRECTS = 10


def retryable_network_error(error):
    cause = error
    while cause is not None:
        if isinstance(cause, ssl.SSLCertVerificationError):
            return False
        cause = cause.__cause__
    return isinstance(
        error, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)
    )


def retryable_response(response):
    return (
        response.status_code
        in (
            HTTPStatus.REQUEST_TIMEOUT,
            HTTPStatus.TOO_MANY_REQUESTS,
        )
        or response.is_server_error
    )


@asynccontextmanager
async def stream_response(client, method, url, headers, event):
    request = client.build_request(method, url, headers=headers)
    for hop in range(MAX_REDIRECTS + 1):
        response = await client.send(request, stream=True, follow_redirects=False)
        try:
            next_request = response.next_request
            if next_request is None:
                yield response
                return
            if hop == MAX_REDIRECTS:
                raise FrameworkError(ErrorCode.DOWNLOAD_REDIRECT_LIMIT)
            target = next_request.url
            if target.scheme not in ("http", "https") or not target.host or target.userinfo:
                raise FrameworkError(ErrorCode.INVALID_APK_URL)
            details = {"hop": hop + 1, "status": response.status_code}
            await event("http.redirect", details)
        finally:
            # HTTPX's automatic redirects read the whole intermediate body.
            await response.aclose()
        request = next_request


def retry_after_seconds(response):
    value = response.headers.get("retry-after", "").strip()
    if not value:
        return None
    if value.isascii() and value.isdecimal():
        value = value.lstrip("0") or "0"
        # A very large wait will be deferred without constructing an enormous integer.
        if len(value) > 10:
            raise FrameworkError(
                ErrorCode.DOWNLOAD_RETRY_DEFERRED, "Retry-After exceeds the supported wait range"
            )
        return float(value)
    try:
        retry_at = parsedate_to_datetime(value)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0, (retry_at - datetime.now(timezone.utc)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None
