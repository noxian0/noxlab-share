from __future__ import annotations

import tempfile
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
from noxlab_share.utils import build_folder_zip, remove_temp_file


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


def main() -> None:
    logs: list[str] = []

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

        server = ShareServer(item=item, start_port=19876, log_callback=logs.append)
        server.start()
        base = f"http://127.0.0.1:{server.port}"
        page = urllib.request.urlopen(f"{base}/download", timeout=5).read().decode("utf-8")
        assert "hello.txt" in page
        assert "/file/hello.txt" in page
        redirected = urllib.request.urlopen(f"{base}/download?download=1", timeout=5)
        assert redirected.url.endswith("/file/hello.txt")
        downloaded = redirected.read()
        assert downloaded == payload
        direct = urllib.request.urlopen(f"{base}/file/hello.txt", timeout=5)
        assert direct.headers["Content-Disposition"].startswith('attachment; filename="hello.txt"')
        assert direct.read() == payload
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

        folder = root / "folder"
        folder.mkdir()
        (folder / "nested.txt").write_text("inside folder", encoding="utf-8")
        zip_path = build_folder_zip(folder)
        assert zip_path.exists()
        assert zip_path.stat().st_size > 0
        remove_temp_file(zip_path)
        assert not zip_path.exists()

        receive_dir = root / "received"
        receive = ReceiveServer(receive_dir, start_port=19890, log_callback=logs.append)
        receive.start()
        base = f"http://127.0.0.1:{receive.port}"
        upload_page = urllib.request.urlopen(f"{base}/upload", timeout=5).read().decode("utf-8")
        assert "Send files to this PC" in upload_page
        upload_payload = b"phone file bytes"
        request = upload_request(f"{base}/upload", [("files", "phone-photo.jpg", upload_payload)])
        response = urllib.request.urlopen(request, timeout=5).read().decode("utf-8")
        assert "Upload complete" in response
        assert (receive_dir / "phone-photo.jpg").read_bytes() == upload_payload
        receive.stop()

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
