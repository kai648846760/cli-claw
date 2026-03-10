from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import uuid
import urllib.error
import urllib.request
from typing import Any


def request_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else ""
        raise RuntimeError(f"HTTP error {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"HTTP request failed: {exc}") from exc

    if not body:
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body}


def _guess_mime(path: str, fallback: str = "application/octet-stream") -> str:
    return mimetypes.guess_type(path)[0] or fallback


def request_multipart(
    url: str,
    fields: dict[str, Any],
    files: dict[str, dict[str, Any]],
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    boundary = f"----cli-claw-{uuid.uuid4().hex}"
    body: list[bytes] = []

    def _add_field(name: str, value: Any) -> None:
        body.append(f"--{boundary}\r\n".encode("utf-8"))
        body.append(f"Content-Disposition: form-data; name=\"{name}\"\r\n\r\n".encode("utf-8"))
        body.append(str(value).encode("utf-8"))
        body.append(b"\r\n")

    def _add_file(name: str, file_info: dict[str, Any]) -> None:
        filename = file_info.get("filename") or name
        content = file_info.get("content")
        mime = file_info.get("mime_type") or _guess_mime(filename)
        if content is None and file_info.get("path"):
            path = str(file_info["path"])
            with open(path, "rb") as fh:
                content = fh.read()
            filename = os.path.basename(path)
            mime = file_info.get("mime_type") or _guess_mime(path)
        if content is None:
            content = b""
        if isinstance(content, str):
            content = content.encode("utf-8")

        body.append(f"--{boundary}\r\n".encode("utf-8"))
        body.append(
            f"Content-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n".encode("utf-8")
        )
        body.append(f"Content-Type: {mime}\r\n\r\n".encode("utf-8"))
        body.append(content)
        body.append(b"\r\n")

    for key, value in fields.items():
        _add_field(key, value)
    for key, file_info in files.items():
        _add_file(key, file_info)

    body.append(f"--{boundary}--\r\n".encode("utf-8"))
    data = b"".join(body)
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        resp_body = exc.read().decode("utf-8") if exc.fp else ""
        raise RuntimeError(f"HTTP error {exc.code}: {resp_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"HTTP request failed: {exc}") from exc

    if not resp_body:
        return {}
    try:
        return json.loads(resp_body)
    except json.JSONDecodeError:
        return {"raw": resp_body}


async def post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    return await asyncio.to_thread(request_json, url, payload, headers, timeout)


async def post_multipart(
    url: str,
    fields: dict[str, Any],
    files: dict[str, dict[str, Any]],
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    return await asyncio.to_thread(request_multipart, url, fields, files, headers, timeout)
