"""Tests for previously untested functions in server.py."""

import json
import os
import ssl
from unittest.mock import patch, MagicMock

import pytest

from server import (
    read_pdf_as_markdown,
    get_conversion_log,
    convert_url_to_markdown,
    convert_all_swagger_in_folder,
    convert_urls_to_markdown,
    _format_ocr_label,
    _check_skip,
    _write_markdown,
    _make_ssl_context,
    _save_log,
    _load_log,
    _file_hash,
    _now_iso,
    EXPORT_SUBFOLDER,
    LOG_FILENAME,
)


# ---------------------------------------------------------------------------
# read_pdf_as_markdown
# ---------------------------------------------------------------------------

class TestReadPdfAsMarkdown:
    def test_file_not_found(self):
        result = read_pdf_as_markdown("/nonexistent/file.pdf")
        assert "Error: file not found" in result

    def test_successful_read(self, tmp_path):
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        with patch("server.pymupdf4llm.to_markdown", return_value="# Title\n\nBody text"):
            result = read_pdf_as_markdown(str(pdf))

        assert "# Title" in result
        assert "Body text" in result

    def test_truncates_long_content(self, tmp_path):
        pdf = tmp_path / "big.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        long_text = "x" * 200_000
        with patch("server.pymupdf4llm.to_markdown", return_value=long_text):
            result = read_pdf_as_markdown(str(pdf))

        assert "truncated" in result
        assert len(result) <= 100_100

    def test_conversion_error(self, tmp_path):
        pdf = tmp_path / "bad.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        with patch("server.pymupdf4llm.to_markdown", side_effect=Exception("parse failure")):
            result = read_pdf_as_markdown(str(pdf))

        assert "Error converting PDF" in result


# ---------------------------------------------------------------------------
# get_conversion_log
# ---------------------------------------------------------------------------

class TestGetConversionLog:
    def test_no_log_file(self, tmp_path):
        result = get_conversion_log(str(tmp_path))
        assert "No conversion log found" in result

    def test_empty_log(self, tmp_path):
        export_dir = tmp_path / EXPORT_SUBFOLDER
        export_dir.mkdir()
        log_file = export_dir / LOG_FILENAME
        log_file.write_text("{}", encoding="utf-8")

        result = get_conversion_log(str(tmp_path))
        assert "empty" in result.lower()

    def test_with_entries(self, tmp_path):
        export_dir = tmp_path / EXPORT_SUBFOLDER
        export_dir.mkdir()
        log_file = export_dir / LOG_FILENAME

        log_data = {
            "/docs/report.pdf": {
                "status": "ok",
                "chars": 5000,
                "lines": 200,
                "converted_at": "2025-01-01T00:00:00+00:00",
                "converted_by": "testuser",
                "machine": "testhost",
                "pages": 10,
                "duration_sec": 2.5,
                "source_size_bytes": 102400,
            },
            "/docs/broken.pdf": {
                "status": "error",
                "error": "corrupt file",
                "converted_at": "2025-01-02T00:00:00+00:00",
                "converted_by": "testuser",
                "machine": "testhost",
                "source_size_bytes": 51200,
            },
        }
        log_file.write_text(json.dumps(log_data), encoding="utf-8")

        result = get_conversion_log(str(tmp_path))
        assert "ok: 1" in result
        assert "errors: 1" in result
        assert "report.pdf" in result
        assert "broken.pdf" in result


# ---------------------------------------------------------------------------
# _format_ocr_label
# ---------------------------------------------------------------------------

class TestFormatOcrLabel:
    def test_empty_stats(self):
        assert _format_ocr_label({}) == ""

    def test_no_total(self):
        assert _format_ocr_label({"images_total": 0}) == ""

    def test_all_ok(self):
        stats = {
            "images_total": 3,
            "images_ocr_ok": 3,
            "images_missing": 0,
            "images_ocr_error": 0,
            "images_ocr_empty": 0,
        }
        result = _format_ocr_label(stats)
        assert "3/3" in result

    def test_with_failures(self):
        stats = {
            "images_total": 5,
            "images_ocr_ok": 2,
            "images_missing": 2,
            "images_ocr_error": 1,
            "images_ocr_empty": 0,
        }
        result = _format_ocr_label(stats)
        assert "2/5" in result
        assert "missing" in result.lower() or "miss" in result.lower()

    def test_compact_mode(self):
        stats = {
            "images_total": 4,
            "images_ocr_ok": 3,
            "images_missing": 1,
            "images_ocr_error": 0,
            "images_ocr_empty": 0,
        }
        result = _format_ocr_label(stats, compact=True)
        assert "+OCR(" in result


# ---------------------------------------------------------------------------
# _check_skip
# ---------------------------------------------------------------------------

class TestCheckSkip:
    def test_returns_none_when_not_converted(self, tmp_path):
        log_file = str(tmp_path / "log.json")
        result = _check_skip({}, log_file, "file.pdf", "hash123", force=False)
        assert result is None

    def test_returns_none_when_force(self, tmp_path):
        out = tmp_path / "out.md"
        out.write_text("content", encoding="utf-8")
        log = {
            "file.pdf": {
                "status": "ok",
                "source_hash": "hash123",
                "output_path": str(out),
                "converted_at": _now_iso(),
            }
        }
        log_file = str(tmp_path / "log.json")
        _save_log(log_file, log)

        result = _check_skip(log, log_file, "file.pdf", "hash123", force=True)
        assert result is None

    def test_returns_skip_message(self, tmp_path):
        out = tmp_path / "out.md"
        out.write_text("content", encoding="utf-8")
        converted_at = _now_iso()
        log = {
            "file.pdf": {
                "status": "ok",
                "source_hash": "hash123",
                "output_path": str(out),
                "converted_at": converted_at,
            }
        }
        log_file = str(tmp_path / "log.json")
        _save_log(log_file, log)

        result = _check_skip(log, log_file, "file.pdf", "hash123", force=False)
        assert result is not None
        assert "Skipped" in result

    def test_updates_skip_count(self, tmp_path):
        out = tmp_path / "out.md"
        out.write_text("content", encoding="utf-8")
        log = {
            "file.pdf": {
                "status": "ok",
                "source_hash": "hash123",
                "output_path": str(out),
                "converted_at": _now_iso(),
            }
        }
        log_file = str(tmp_path / "log.json")
        _save_log(log_file, log)

        _check_skip(log, log_file, "file.pdf", "hash123", force=False)

        saved_log = _load_log(log_file)
        assert saved_log["file.pdf"]["skip_count"] >= 1


# ---------------------------------------------------------------------------
# _write_markdown
# ---------------------------------------------------------------------------

class TestWriteMarkdown:
    def test_creates_file(self, tmp_path):
        path = str(tmp_path / "output.md")
        _write_markdown(path, "# Hello\n\nWorld")
        assert os.path.isfile(path)
        with open(path, encoding="utf-8") as f:
            assert f.read() == "# Hello\n\nWorld"

    def test_creates_directories(self, tmp_path):
        path = str(tmp_path / "a" / "b" / "c" / "output.md")
        _write_markdown(path, "nested content")
        assert os.path.isfile(path)


# ---------------------------------------------------------------------------
# _make_ssl_context
# ---------------------------------------------------------------------------

class TestMakeSslContext:
    def test_returns_ssl_context(self):
        ctx = _make_ssl_context()
        assert isinstance(ctx, ssl.SSLContext)

    def test_no_verify(self):
        ctx = _make_ssl_context()
        assert ctx.check_hostname is False


# ---------------------------------------------------------------------------
# convert_all_swagger_in_folder (async)
# ---------------------------------------------------------------------------

class TestConvertAllSwaggerInFolder:
    @pytest.mark.asyncio
    async def test_empty_folder(self, tmp_path):
        result = await convert_all_swagger_in_folder(str(tmp_path))
        assert "No Swagger/OpenAPI files found" in result

    @pytest.mark.asyncio
    async def test_dir_not_found(self):
        result = await convert_all_swagger_in_folder("/nonexistent/path/xyz")
        assert "Error: directory not found" in result


# ---------------------------------------------------------------------------
# convert_url_to_markdown (sync)
# ---------------------------------------------------------------------------

class TestConvertUrlToMarkdown:
    def test_invalid_url(self):
        result = convert_url_to_markdown("not-a-url")
        assert "Error" in result


# ---------------------------------------------------------------------------
# convert_urls_to_markdown (async)
# ---------------------------------------------------------------------------

class TestConvertUrlsToMarkdown:
    @pytest.mark.asyncio
    async def test_no_urls(self):
        result = await convert_urls_to_markdown("")
        assert "Error: no URLs provided" in result

    @pytest.mark.asyncio
    async def test_invalid_url_skipped(self):
        result = await convert_urls_to_markdown("not-a-url")
        assert "SKIP" in result
