import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from hashlib import sha256

from im_archive_cli.media import (
    attachment_filename,
    extract_inline_image_attachment,
    iter_inline_image_attachments,
)
from im_archive_cli.config import AppConfig
from im_archive_cli.media_download import download_conversation_images


class _DummyLogger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, message: str) -> None:
        self.lines.append(message)


def test_extract_inline_image_attachment_parses_message_body_fields() -> None:
    message_body = json.dumps(
        {
            "url": "https://cdn.example.com/images/chat_01.webp",
            "thumbUrl": "https://cdn.example.com/images/chat_01_t.webp",
            "width": 640,
            "height": 360,
            "btype": 1,
            "imagePath": "/images/chat_01.webp",
            "thumbPath": "/images/chat_01_t.webp",
            "ext": ".webp",
        }
    )

    result = extract_inline_image_attachment(message_body)

    assert result == {
        "src": "https://cdn.example.com/images/chat_01.webp",
        "thumbSrc": "https://cdn.example.com/images/chat_01_t.webp",
        "width": "640",
        "height": "360",
        "source": "messageBody",
        "btype": "1",
        "imagePath": "/images/chat_01.webp",
        "thumbPath": "/images/chat_01_t.webp",
    }


def test_extract_inline_image_attachment_prefers_origin_image_url() -> None:
    message_body = json.dumps(
        {
            "originImageUrl": "https://cdn.example.com/images/origin_01.png",
            "url": "https://cdn.example.com/images/fallback_01.gif",
            "thumbUrl": "https://cdn.example.com/images/origin_01_t.png",
            "width": "512",
            "height": "512",
            "btype": "image",
            "imagePath": "/images/origin_01.png",
            "thumbPath": "/images/origin_01_t.png",
            "ext": ".png",
        }
    )

    result = extract_inline_image_attachment(message_body)

    assert result is not None
    assert result["src"] == "https://cdn.example.com/images/origin_01.png"
    assert result["thumbSrc"] == "https://cdn.example.com/images/origin_01_t.png"


def test_extract_inline_image_attachment_falls_back_to_url_when_origin_not_image() -> None:
    message_body = json.dumps(
        {
            "originImageUrl": "https://www.trip.com/order/detail/TC1234567890",
            "url": "https://cdn.example.com/images/recovered_01.png",
            "thumbUrl": "https://www.trip.com/order/thumb/TC1234567890",
            "width": 400,
            "height": 300,
            "btype": "image",
            "imagePath": "/images/recovered_01.png",
            "thumbPath": "/images/recovered_01_t.png",
            "ext": ".png",
        }
    )

    result = extract_inline_image_attachment(message_body)

    assert result is not None
    assert result["src"] == "https://cdn.example.com/images/recovered_01.png"


def test_extract_inline_image_attachment_ignores_system_avatar_json() -> None:
    message_body = json.dumps(
        {
            "infos": [
                {
                    "avatar": {
                        "url": "https://avatars.example.com/staff_01.png",
                        "thumbUrl": "https://avatars.example.com/staff_01_t.png",
                    }
                }
            ]
        }
    )

    assert extract_inline_image_attachment(message_body) is None


def test_extract_inline_image_attachment_rejects_non_image_order_url() -> None:
    message_body = json.dumps(
        {
            "url": "https://www.trip.com/order/detail/TC1234567890",
            "thumbUrl": "https://www.trip.com/orders/TC1234567890",
            "btype": "link",
            "width": 0,
            "height": 0,
            "imagePath": "",
            "thumbPath": "",
            "ext": "",
        }
    )

    assert extract_inline_image_attachment(message_body) is None


def test_iter_inline_image_attachments_filters_source_and_src() -> None:
    messages = [
        {
            "sequence": 7,
            "attachments": [
                {
                    "source": "messageBody",
                    "src": "https://cdn.example.com/images/valid_01.jpg",
                    "btype": 1,
                },
                {"source": "messageBody"},
                {"source": "other", "src": "https://cdn.example.com/images/other.jpg"},
            ],
        },
        {"sequence": "8", "attachments": {"source": "messageBody", "src": "https://cdn.example.com/images/bad_attachments_type.jpg"}},
        {"sequence": 9, "attachments": [1, 2, 3]},
        {
            "sequence": 10,
            "attachments": [
                {
                    "source": "messageBody",
                    "src": "https://cdn.example.com/images/no_src.jpg",
                }
            ],
        },
    ]

    results = list(iter_inline_image_attachments(messages))

    assert len(results) == 2
    assert results[0][0] == 7
    assert results[0][1]["src"] == "https://cdn.example.com/images/valid_01.jpg"
    assert results[1][0] == 10
    assert results[1][1]["src"] == "https://cdn.example.com/images/no_src.jpg"
    assert all(attachment["source"] == "messageBody" for _, attachment in results)


def test_iter_inline_image_attachments_skips_non_dict_messages() -> None:
    messages = [
        None,
        "invalid",
        123,
        {
            "sequence": 3,
            "attachments": [
                {"source": "messageBody", "src": "https://cdn.example.com/images/only_01.gif"},
                {"source": "messageBody"},
            ],
        },
        [1, 2, 3],
    ]

    results = list(iter_inline_image_attachments(messages))

    assert len(results) == 1
    assert results[0][0] == 3
    assert results[0][1]["src"] == "https://cdn.example.com/images/only_01.gif"


def test_attachment_filename_is_stable_and_preserves_extension() -> None:
    url_jpeg = "https://cdn.example.com/images/abc/attachment.JPEG?x=1"
    noext_url = "https://cdn.example.com/images/noext"
    seq = 31

    jpeg_filename = attachment_filename(seq, url_jpeg)
    noext_filename = attachment_filename(seq, noext_url, content_type="image/jpeg")

    assert jpeg_filename == f"seq{seq:04d}_{sha256(url_jpeg.encode('utf-8')).hexdigest()[:12]}.jpg"
    assert noext_filename == f"seq{seq:04d}_{sha256(noext_url.encode('utf-8')).hexdigest()[:12]}.jpg"


def test_attachment_filename_non_image_content_type_falls_back_to_bin() -> None:
    url = "https://cdn.example.com/images/noext"
    seq = 8

    filename = attachment_filename(seq, url, content_type="application/octet-stream")

    assert filename == f"seq{seq:04d}_{sha256(url.encode('utf-8')).hexdigest()[:12]}.bin"


def test_download_conversation_images_writes_local_file_and_relative_path(tmp_path: Path) -> None:
    image_bytes = b"fake-image-bytes"

    class ImageHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("content-type", "image/png; charset=utf-8")
            self.send_header("content-length", str(len(image_bytes)))
            self.end_headers()
            self.wfile.write(image_bytes)

        def log_message(self, _format: str, *_args) -> None:  # noqa: ARG002
            return

    server = HTTPServer(("127.0.0.1", 0), ImageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        image_url = f"http://127.0.0.1:{server.server_port}/image.png"
        conversation = {
            "messages": [
                {
                    "sequence": 1,
                    "attachments": [
                        {
                            "src": image_url,
                            "thumbSrc": image_url,
                            "source": "messageBody",
                        }
                    ],
                }
            ]
        }
        conversation_dir = tmp_path / "conversation"
        conversation_dir.mkdir()
        base_name = "IMChatlogExport_20260629_s1_alice"
        cfg = AppConfig(image_request_interval_sec=0)
        log = _DummyLogger()

        download_conversation_images(conversation, conversation_dir, base_name, cfg, log.info)

        attachment = conversation["messages"][0]["attachments"][0]
        assert attachment["downloadStatus"] == "downloaded"
        assert attachment["relativePath"].startswith(f"{base_name}_assets/")
        assert Path(attachment["localPath"]).is_absolute()
        assert Path(attachment["localPath"]).read_bytes() == image_bytes
        assert (conversation_dir / attachment["relativePath"]).exists()
    finally:
        server.shutdown()
        server.server_close()


def test_download_conversation_images_non_image_does_not_raise_and_clears_old_state(tmp_path: Path) -> None:
    class TextHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("content-type", "text/plain; charset=utf-8")
            self.send_header("content-length", "4")
            self.end_headers()
            self.wfile.write(b"not-image")

        def log_message(self, _format: str, *_args) -> None:  # noqa: ARG002
            return

    server = HTTPServer(("127.0.0.1", 0), TextHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        image_url = f"http://127.0.0.1:{server.server_port}/text.txt"
        conversation = {
            "messages": [
                {
                    "sequence": 2,
                    "attachments": [
                        {
                            "src": image_url,
                            "thumbSrc": image_url,
                            "source": "messageBody",
                            "downloadStatus": "downloaded",
                            "localPath": "/old/path.png",
                            "relativePath": "old/assets/file.png",
                        }
                    ],
                }
            ]
        }
        conversation_dir = tmp_path / "conversation"
        conversation_dir.mkdir()
        base_name = "IMChatlogExport_20260629_s2_bob"
        cfg = AppConfig(image_request_interval_sec=0)
        logger = _DummyLogger()

        download_conversation_images(conversation, conversation_dir, base_name, cfg, logger.info)

        attachment = conversation["messages"][0]["attachments"][0]
        assert attachment["downloadStatus"] == "failed"
        assert "downloadError" in attachment
        assert "localPath" not in attachment
        assert "relativePath" not in attachment
        assert not (conversation_dir / "old" / "assets" / "file.png").exists()
    finally:
        server.shutdown()
        server.server_close()


def test_download_conversation_images_marks_failure_on_http_error_and_does_not_raise(tmp_path: Path) -> None:
    class ErrorHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(500)
            self.send_header("content-type", "image/png")
            self.end_headers()

        def log_message(self, _format: str, *_args) -> None:  # noqa: ARG002
            return

    server = HTTPServer(("127.0.0.1", 0), ErrorHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        image_url = f"http://127.0.0.1:{server.server_port}/missing.png"
        conversation = {
            "messages": [
                {
                    "sequence": 3,
                    "attachments": [
                        {"src": image_url, "thumbSrc": image_url, "source": "messageBody"},
                    ],
                }
            ]
        }
        conversation_dir = tmp_path / "conversation"
        conversation_dir.mkdir()
        base_name = "IMChatlogExport_20260629_s3_charlie"
        cfg = AppConfig(image_request_interval_sec=0)

        download_conversation_images(conversation, conversation_dir, base_name, cfg, None)

        attachment = conversation["messages"][0]["attachments"][0]
        assert attachment["downloadStatus"] == "failed"
        assert "downloadError" in attachment
    finally:
        server.shutdown()
        server.server_close()


def test_download_conversation_images_clears_partial_part_file_on_size_limit(tmp_path: Path) -> None:
    class BigImageHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("content-type", "image/png")
            self.send_header("content-length", "20")
            self.end_headers()
            self.wfile.write(b"x" * 20)

        def log_message(self, _format: str, *_args) -> None:  # noqa: ARG002
            return

    server = HTTPServer(("127.0.0.1", 0), BigImageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        image_url = f"http://127.0.0.1:{server.server_port}/big.png"
        conversation = {
            "messages": [
                {
                    "sequence": 4,
                    "attachments": [
                        {"src": image_url, "thumbSrc": image_url, "source": "messageBody"},
                    ],
                }
            ]
        }
        conversation_dir = tmp_path / "conversation"
        conversation_dir.mkdir()
        base_name = "IMChatlogExport_20260629_s4_dana"
        cfg = AppConfig(image_request_interval_sec=0, image_max_bytes=10)

        download_conversation_images(conversation, conversation_dir, base_name, cfg, None)

        attachment = conversation["messages"][0]["attachments"][0]
        assert attachment["downloadStatus"] == "failed"
        assert "downloadError" in attachment
        assert list((conversation_dir / f"{base_name}_assets").glob("*.part")) == []
        assert "relativePath" not in attachment
        assert "localPath" not in attachment
    finally:
        server.shutdown()
        server.server_close()
