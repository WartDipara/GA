import asyncio
import hashlib
import json
import os
import re
import zipfile
import zlib
from http import HTTPStatus

import httpx

from game_automation.common import FrameworkError, atomic_json, digest, within
from game_automation.errors import ErrorCode

CHUNK_BYTES = 256 * 1024
CHECKPOINT_BYTES = 8 * 1024 * 1024
CONTENT_RANGE = re.compile(r"bytes ([0-9]+)-([0-9]+)/([0-9]+)")
STRONG_ETAG = re.compile(r'"[\x21\x23-\x7e]*"')


def file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_archive(path, max_bytes):
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 200_000 or sum(entry.file_size for entry in entries) > max_bytes * 20:
                raise FrameworkError(ErrorCode.APK_ARCHIVE_LIMIT)
            if "AndroidManifest.xml" not in archive.namelist() or archive.testzip() is not None:
                raise ValueError
    except (
        ValueError,
        zipfile.BadZipFile,
        RuntimeError,
        NotImplementedError,
        EOFError,
        zlib.error,
    ) as exc:
        raise FrameworkError(ErrorCode.INVALID_APK) from exc


class Downloader:
    def __init__(self, workspace, request, event):
        self.request = request
        self.event = event
        self.partial = within(workspace, "download.part")
        self.final = within(workspace, "application.apk")
        self.state_path = within(workspace, "download.json")
        self.state = {}

    def checkpoint(self, stream, checksum):
        stream.flush()
        os.fsync(stream.fileno())
        self.state["downloaded_bytes"] = stream.tell()
        self.state["prefix_sha256"] = checksum.hexdigest()
        atomic_json(self.state_path, self.state)

    async def restore(self):
        if self.state_path.exists():
            try:
                self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
                if self.state["source_id"] != self.request["source_id"]:
                    raise ValueError
                count = self.state["downloaded_bytes"]
                if not isinstance(count, int) or not 0 <= count <= self.request["max_apk_bytes"]:
                    raise ValueError
            except (ValueError, KeyError, TypeError) as exc:
                raise FrameworkError(ErrorCode.DOWNLOAD_STATE_INVALID) from exc
        if self.final.exists():
            actual = await asyncio.to_thread(file_digest, self.final)
            if actual != self.state.get("prefix_sha256"):
                raise FrameworkError(ErrorCode.LOCAL_APK_CHANGED)
            await self.event(
                "download.reused", {"sha256": actual, "sizeBytes": self.final.stat().st_size}
            )
            return True
        if self.partial.exists() and self.state:
            count = self.state["downloaded_bytes"]
            if self.partial.stat().st_size >= count:
                with self.partial.open("r+b") as stream:
                    stream.truncate(count)
                actual = await asyncio.to_thread(file_digest, self.partial)
                if actual != self.state.get("prefix_sha256"):
                    raise FrameworkError(ErrorCode.LOCAL_DOWNLOAD_CHANGED)
                return False
        self.state = {}
        return False

    async def response_plan(self, response, offset, resume, etag, attempt):
        response_payload = {
            "attempt": attempt,
            "status": response.status_code,
            "offset": offset,
        }
        await self.event("download.response", response_payload)
        if response.status_code == HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE and resume:
            self.state = {}
            raise FrameworkError(ErrorCode.DOWNLOAD_RESTART, retryable=True)
        if response.status_code not in (HTTPStatus.OK, HTTPStatus.PARTIAL_CONTENT):
            retryable = (
                response.status_code in (HTTPStatus.REQUEST_TIMEOUT, HTTPStatus.TOO_MANY_REQUESTS)
                or response.is_server_error
            )
            raise FrameworkError(
                ErrorCode.DOWNLOAD_HTTP_ERROR,
                f"Download returned HTTP {response.status_code}",
                retryable=retryable,
            )
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            raise FrameworkError(ErrorCode.DOWNLOAD_ENCODING)
        total = None
        end = None
        resource_id = digest(str(response.url).encode("utf-8"))
        if response.status_code == HTTPStatus.PARTIAL_CONTENT:
            match = CONTENT_RANGE.fullmatch(response.headers.get("content-range", ""))
            if not match or not resume:
                raise FrameworkError(ErrorCode.DOWNLOAD_RANGE_INVALID)
            start, end, total = map(int, match.groups())
            valid_range = start == offset and start <= end < total
            received_etag = response.headers.get("etag")
            same_resource = received_etag == etag and resource_id == self.state.get("resource_id")
            same_size = self.state.get("total_bytes") in (None, total)
            if not valid_range or not same_resource or not same_size:
                self.state = {}
                raise FrameworkError(
                    ErrorCode.DOWNLOAD_RESTART,
                    "Resource changed; restarting download",
                    retryable=True,
                )
        else:
            if resume:
                restart = {"attempt": attempt, "reason": "Server returned a full response"}
                await self.event("download.restarted", restart)
            offset = 0
        raw_length = response.headers.get("content-length")
        if raw_length is not None:
            if not raw_length.isdecimal():
                raise FrameworkError(ErrorCode.DOWNLOAD_LENGTH_INVALID)
            length = int(raw_length)
            if end is not None and length != end - offset + 1:
                raise FrameworkError(
                    ErrorCode.DOWNLOAD_LENGTH_INVALID, "Range and Content-Length disagree"
                )
            if response.status_code == HTTPStatus.OK:
                total = length
        if total is not None and total > self.request["max_apk_bytes"]:
            raise FrameworkError(ErrorCode.APK_TOO_LARGE)
        plan = {"offset": offset, "end": end, "total": total, "resource_id": resource_id}
        return plan

    async def transfer(self, client, url, attempt):
        offset = self.state.get("downloaded_bytes", 0)
        etag = self.state.get("etag")
        resume = bool(offset and etag and STRONG_ETAG.fullmatch(etag))
        headers = {"Accept-Encoding": "identity"}
        if resume:
            headers["Range"] = f"bytes={offset}-"
            headers["If-Range"] = etag
        else:
            offset = 0
        request_payload = {"attempt": attempt, "offset": offset, "resuming": resume}
        await self.event("download.request", request_payload)
        async with client.stream("GET", url, headers=headers) as response:
            plan = await self.response_plan(response, offset, resume, etag, attempt)
            offset = plan["offset"]
            end = plan["end"]
            total = plan["total"]
            self.state = {
                "source_id": self.request["source_id"],
                "etag": response.headers.get("etag"),
                "resource_id": plan["resource_id"],
                "total_bytes": total,
            }
            mode = "r+b" if offset else "wb"
            checksum = hashlib.sha256()
            with self.partial.open(mode) as stream:
                if offset:
                    while chunk := stream.read(CHUNK_BYTES):
                        checksum.update(chunk)
                stream.seek(offset)
                self.checkpoint(stream, checksum)
                checkpoint_offset = offset
                try:
                    async for chunk in response.aiter_raw(CHUNK_BYTES):
                        count = stream.tell() + len(chunk)
                        if count > self.request["max_apk_bytes"] or (
                            total is not None and count > total
                        ):
                            raise FrameworkError(
                                ErrorCode.APK_TOO_LARGE,
                                "Response exceeds the allowed download size",
                            )
                        if end is not None and count > end + 1:
                            raise FrameworkError(
                                ErrorCode.DOWNLOAD_RANGE_INVALID,
                                "Response exceeds its declared range",
                            )
                        stream.write(chunk)
                        checksum.update(chunk)
                        if count - checkpoint_offset >= CHECKPOINT_BYTES:
                            self.checkpoint(stream, checksum)
                            progress = {"downloaded_bytes": count, "total_bytes": total}
                            await self.event("download.progress", progress)
                            checkpoint_offset = count
                finally:
                    self.checkpoint(stream, checksum)
            count = self.state["downloaded_bytes"]
            if (total is not None and count != total) or (end is not None and count != end + 1):
                raise FrameworkError(
                    ErrorCode.DOWNLOAD_INCOMPLETE,
                    retryable=True,
                )
            self.state["transfer_complete"] = True
            atomic_json(self.state_path, self.state)

    async def finalize(self):
        try:
            await asyncio.to_thread(validate_archive, self.partial, self.request["max_apk_bytes"])
        except FrameworkError:
            self.state["transfer_complete"] = False
            self.state["etag"] = None
            atomic_json(self.state_path, self.state)
            raise
        evidence = {
            "sizeBytes": self.state["downloaded_bytes"],
            "sha256": self.state["prefix_sha256"],
        }
        await self.event("download.verified", evidence)
        self.partial.replace(self.final)
        return self.final

    async def run(self, url):
        if await self.restore():
            return self.final
        if self.state.get("transfer_complete"):
            return await self.finalize()
        timeout = httpx.Timeout(self.request["request_timeout_seconds"])
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            for attempt in range(1, self.request["max_attempts"] + 1):
                try:
                    await self.transfer(client, url, attempt)
                    return await self.finalize()
                except httpx.HTTPError as exc:
                    error = FrameworkError(
                        ErrorCode.DOWNLOAD_NETWORK_ERROR, type(exc).__name__, retryable=True
                    )
                except FrameworkError as exc:
                    if exc.code == ErrorCode.TRACE_UNAVAILABLE:
                        raise
                    error = exc
                failed_payload = {
                    "attempt": attempt,
                    "error": error.as_dict(),
                    "downloaded_bytes": self.state.get("downloaded_bytes", 0),
                }
                await self.event("download.attempt_failed", failed_payload)
                if not error.retryable or attempt == self.request["max_attempts"]:
                    raise error
                await asyncio.sleep(min(2 ** (attempt - 1), 10))
