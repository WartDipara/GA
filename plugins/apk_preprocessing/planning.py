from http import HTTPStatus

import httpx
from download import CONTENT_RANGE

from game_automation.common import FrameworkError, within
from game_automation.errors import ErrorCode


def response_size(response):
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        return None
    if response.status_code == HTTPStatus.PARTIAL_CONTENT:
        match = CONTENT_RANGE.fullmatch(response.headers.get("content-range", ""))
        if match:
            start, end, total = map(int, match.groups())
            if start == end == 0 and total > 0:
                return total
    elif response.status_code == HTTPStatus.OK:
        length = response.headers.get("content-length", "")
        if length.isdecimal() and int(length) > 0:
            return int(length)
    return None


async def plan_download(url, workspace, request, event):
    final = within(workspace, "application.apk")
    if final.is_file():
        plan = {"budget_bytes": 0, "size_bytes": final.stat().st_size, "basis": "local_apk"}
        return plan

    headers = {"Accept-Encoding": "identity"}
    timeout = httpx.Timeout(request["request_timeout_seconds"])
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for method in ("HEAD", "GET"):
            if method == "GET":
                headers["Range"] = "bytes=0-0"
            try:
                # Read headers only, including when the server ignores Range.
                async with client.stream(method, url, headers=headers) as response:
                    size = response_size(response)
                    details = {"method": method, "status": response.status_code, "size_bytes": size}
                    await event("storage.probe", details)
                    if size is None:
                        continue
                    if size > request["max_apk_bytes"]:
                        raise FrameworkError(ErrorCode.APK_TOO_LARGE)
                    basis = (
                        "content_range"
                        if response.status_code == HTTPStatus.PARTIAL_CONTENT
                        else "content_length"
                    )
                    plan = {"budget_bytes": size, "size_bytes": size, "basis": basis}
                    return plan
            except httpx.HTTPError as exc:
                details = {"method": method, "error": type(exc).__name__}
                await event("storage.probe_unavailable", details)

    plan = {"budget_bytes": request["max_apk_bytes"], "size_bytes": None, "basis": "size_limit"}
    return plan
