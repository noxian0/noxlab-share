from __future__ import annotations

import tempfile
import threading
import socket
import time
from unittest.mock import patch
from dataclasses import replace
from io import BytesIO
import http.cookiejar
import urllib.error
import urllib.parse
import urllib.request
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from noxlab_share.server import ReceiveServer, ShareItem, ShareServer
from noxlab_share.incoming import CHUNK_SIZE, UploadError
from noxlab_share.utils import ZipPreparationCancelled, build_folder_zip, remove_temp_file


def multipart_body(parts: list[tuple[str, str | None, bytes]], boundary: str) -> bytes:
    body = bytearray()
    for name, filename, value in parts:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        if filename:
            body.extend(
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode("utf-8")
            )
            body.extend(b"Content-Type: application/octet-stream\r\n\r\n")
        else:
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(value)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return bytes(body)


def upload_request(url: str, parts: list[tuple[str, str | None, bytes]], boundary: str = "----NoxLabSmokeBoundary"):
    body = multipart_body(parts, boundary)
    return urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )


def expect_continue_upload(host: str, port: int, filename: str, payload: bytes) -> bytes:
    boundary = "----NoxLabExpectBoundary"
    body = multipart_body([("files", filename, payload)], boundary)
    request = (
        f"POST /upload HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Content-Type: multipart/form-data; boundary={boundary}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Expect: 100-continue\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")

    with socket.create_connection((host, port), timeout=5) as connection:
        connection.sendall(request)
        interim = connection.recv(1024)
        assert b"100 Continue" in interim
        connection.sendall(body)
        response = bytearray()
        while chunk := connection.recv(8192):
            response.extend(chunk)
    return bytes(response)


def wait_for(predicate, timeout: float = 2) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for the server event.")


def main() -> None:
    logs: list[str] = []
    completed_downloads = []

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        file_path = root / "hello.txt"
        payload = b"hello from noxlab share"
        file_path.write_bytes(payload)

        item = ShareItem(
            source_path=file_path,
            served_path=file_path,
            display_name="hello.txt",
            served_name="hello.txt",
            original_size=len(payload),
            served_size=len(payload),
            is_folder=False,
        )

        server = ShareServer(
            item=item,
            start_port=19876,
            log_callback=logs.append,
            download_complete_callback=completed_downloads.append,
        )
        server.start()
        base = f"http://127.0.0.1:{server.port}"
        page = urllib.request.urlopen(f"{base}/download", timeout=5).read().decode("utf-8")
        assert "hello.txt" in page
        assert "/file/hello.txt" in page
        redirected = urllib.request.urlopen(f"{base}/download?download=1", timeout=5)
        assert redirected.url.endswith("/file/hello.txt")
        downloaded = redirected.read()
        assert downloaded == payload
        wait_for(lambda: len(completed_downloads) >= 1)
        assert completed_downloads[0].device_ip == "127.0.0.1"
        assert completed_downloads[0].item_name == "hello.txt"
        assert not completed_downloads[0].resumed
        assert completed_downloads[0].transferred_bytes == len(payload)
        assert completed_downloads[0].total_bytes == len(payload)
        direct = urllib.request.urlopen(f"{base}/file/hello.txt", timeout=5)
        assert direct.headers["Content-Disposition"].startswith('attachment; filename="hello.txt"')
        assert direct.headers["Accept-Ranges"] == "bytes"
        assert direct.read() == payload
        wait_for(lambda: len(completed_downloads) >= 2)

        partial_request = urllib.request.Request(
            f"{base}/file/hello.txt",
            headers={"Range": "bytes=6-"},
        )
        partial = urllib.request.urlopen(partial_request, timeout=5)
        assert partial.status == 206
        assert partial.headers["Content-Range"] == f"bytes 6-{len(payload) - 1}/{len(payload)}"
        assert partial.read() == payload[6:]
        wait_for(lambda: len(completed_downloads) >= 3)
        assert completed_downloads[-1].resumed
        assert completed_downloads[-1].transferred_bytes == len(payload) - 6

        suffix_request = urllib.request.Request(
            f"{base}/file/hello.txt",
            headers={"Range": "bytes=-5"},
        )
        suffix = urllib.request.urlopen(suffix_request, timeout=5)
        assert suffix.status == 206
        assert suffix.read() == payload[-5:]

        invalid_range_request = urllib.request.Request(
            f"{base}/file/hello.txt",
            headers={"Range": f"bytes={len(payload)}-"},
        )
        try:
            urllib.request.urlopen(invalid_range_request, timeout=5)
            raise AssertionError("invalid byte range unexpectedly succeeded")
        except urllib.error.HTTPError as exc:
            assert exc.code == 416
            assert exc.headers["Content-Range"] == f"bytes */{len(payload)}"

        server._stopping.set()
        try:
            server._copy_file_range(BytesIO(payload), BytesIO(), len(payload))
            raise AssertionError("stopped server continued copying")
        except ConnectionAbortedError:
            pass
        server._stopping.clear()
        server.stop()

        protected = ShareServer(item=item, password="secret", start_port=19876, log_callback=logs.append)
        protected.start()
        base = f"http://127.0.0.1:{protected.port}"
        try:
            urllib.request.urlopen(f"{base}/file/hello.txt", timeout=5)
            raise AssertionError("protected GET unexpectedly downloaded")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401

        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        body = urllib.parse.urlencode({"password": "secret"}).encode("utf-8")
        request = urllib.request.Request(f"{base}/download", data=body, method="POST")
        protected_response = opener.open(request, timeout=5)
        assert protected_response.url.endswith("/file/hello.txt")
        protected_download = protected_response.read()
        assert protected_download == payload
        protected.stop()

        empty_path = root / "empty"
        empty_path.touch()
        empty_server = ShareServer(replace(item, served_name="\u5f71\u7247.mp4", served_path=empty_path),
                                   start_port=19876, download_complete_callback=completed_downloads.append)
        empty_server.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{empty_server.port}" + empty_server._file_url()) as reply:
                assert reply.read() == b""
                assert urllib.parse.quote(empty_server.item.served_name) in reply.headers["Content-Disposition"]
            wait_for(lambda: completed_downloads[-1].item_name == empty_server.item.served_name)
        finally:
            empty_server.stop()

        folder = root / "folder"
        folder.mkdir()
        (folder / "nested.txt").write_text("inside folder", encoding="utf-8")
        progress: list[tuple[int, int, Path]] = []
        zip_path = build_folder_zip(
            folder,
            total_bytes=len("inside folder"),
            progress_callback=lambda completed, total, current: progress.append((completed, total, current)),
        )
        assert zip_path.exists()
        assert zip_path.stat().st_size > 0
        assert progress[-1][0] == len("inside folder")
        assert progress[-1][1] == len("inside folder")
        remove_temp_file(zip_path)
        assert not zip_path.exists()

        with patch("zipfile.ZipFile.write", side_effect=OSError("simulated disk write failure")):
            try:
                build_folder_zip(folder)
                raise AssertionError("A failed ZIP write was reported as a successful archive")
            except OSError as exc:
                assert "simulated" in str(exc)

        cancelled = threading.Event()
        cancelled.set()
        try:
            build_folder_zip(folder, cancel_event=cancelled)
            raise AssertionError("cancelled folder ZIP unexpectedly succeeded")
        except ZipPreparationCancelled:
            pass

        receive_dir = root / "received"
        receive = ReceiveServer(receive_dir, start_port=19890, log_callback=logs.append)
        receive.start()
        base = f"http://127.0.0.1:{receive.port}"
        upload_page = urllib.request.urlopen(f"{base}/upload", timeout=5).read().decode("utf-8")
        assert "Send files to this PC" in upload_page
        assert 'src="/upload.js"' in upload_page
        assert "opened the receive page" in logs[-1]
        expect_response = expect_continue_upload("127.0.0.1", receive.port, "expect.txt", b"expect continue")
        assert b"HTTP/1.1 200 OK" in expect_response
        assert b"Upload complete" in expect_response
        assert (receive_dir / "expect.txt").read_bytes() == b"expect continue"
        upload_payload = b"phone file bytes"
        request = upload_request(f"{base}/upload", [("files", "phone-photo.jpg", upload_payload)])
        response = urllib.request.urlopen(request, timeout=5).read().decode("utf-8")
        assert "Upload complete" in response
        assert (receive_dir / "phone-photo.jpg").read_bytes() == upload_payload
        assert any("started an upload" in entry for entry in logs)
        staged = receive.incoming
        metadata = {"name": "unfinished.mp4", "size": CHUNK_SIZE + 1}
        token = staged.begin(metadata, "test")["token"]
        try:
            staged.append(token, CHUNK_SIZE, b"x", "test")
            raise AssertionError("Accepted a chunk out of order")
        except UploadError as exc:
            assert exc.status == 409
        staged.append(token, 0, bytes(CHUNK_SIZE), "test")
        assert not (receive_dir / "unfinished.mp4").exists()
        staged.discard(token)
        assert not list(receive_dir.glob(".noxlab-*.part"))
        staged.begin(metadata, "test")
        receive.stop()
        assert not list(receive_dir.glob(".noxlab-*.part"))

        limited_dir = root / "limited-received"
        limited_receive = ReceiveServer(
            limited_dir,
            start_port=19890,
            log_callback=logs.append,
            max_upload_bytes=8,
        )
        limited_receive.start()
        base = f"http://127.0.0.1:{limited_receive.port}"
        limited_request = upload_request(f"{base}/upload", [("files", "too-big.txt", b"too big")])
        try:
            urllib.request.urlopen(limited_request, timeout=5)
            raise AssertionError("limited upload unexpectedly succeeded")
        except urllib.error.HTTPError as exc:
            assert exc.code == 413
        assert not (limited_dir / "too-big.txt").exists()
        limited_receive.stop()

        unlimited_dir = root / "unlimited-received"
        unlimited_receive = ReceiveServer(
            unlimited_dir,
            start_port=19890,
            log_callback=logs.append,
            max_upload_bytes=None,
        )
        unlimited_receive.start()
        base = f"http://127.0.0.1:{unlimited_receive.port}"
        unlimited_page = urllib.request.urlopen(f"{base}/upload", timeout=5).read().decode("utf-8")
        assert "No fixed limit" in unlimited_page
        unlimited_payload = b"upload with no fixed app limit"
        unlimited_request = upload_request(f"{base}/upload", [("files", "unlimited.txt", unlimited_payload)])
        unlimited_response = urllib.request.urlopen(unlimited_request, timeout=5).read().decode("utf-8")
        assert "Upload complete" in unlimited_response
        assert (unlimited_dir / "unlimited.txt").read_bytes() == unlimited_payload
        unlimited_receive.stop()

        protected_receive_dir = root / "protected-received"
        protected_receive = ReceiveServer(
            protected_receive_dir,
            password="secret",
            start_port=19890,
            log_callback=logs.append,
        )
        protected_receive.start()
        base = f"http://127.0.0.1:{protected_receive.port}"
        bad_request = upload_request(f"{base}/upload", [("password", None, b"wrong"), ("files", "bad.txt", b"bad")])
        try:
            urllib.request.urlopen(bad_request, timeout=5)
            raise AssertionError("protected upload unexpectedly succeeded")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        assert not (protected_receive_dir / "bad.txt").exists()

        good_payload = b"secret upload"
        good_request = upload_request(
            f"{base}/upload",
            [("password", None, b"secret"), ("files", "secret.txt", good_payload)],
        )
        good_response = urllib.request.urlopen(good_request, timeout=5).read().decode("utf-8")
        assert "Upload complete" in good_response
        assert (protected_receive_dir / "secret.txt").read_bytes() == good_payload
        protected_receive.stop()

    print("server smoke ok")
    print(f"log entries: {len(logs)}")


if __name__ == "__main__":
    main()
