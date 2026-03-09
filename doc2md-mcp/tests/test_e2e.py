"""End-to-end integration tests — real conversions with result verification.

These tests use real pymupdf4llm, real file I/O, and a local HTTP server.
Heavy dependencies (easyocr) are only used in tests marked @pytest.mark.slow.
"""

import asyncio
import json
import os
import pathlib
import shutil

import pymupdf
import pytest

from server import (
    convert_pdf_to_markdown,
    convert_swagger_to_markdown,
    convert_api_url_to_markdown,
    convert_all_pdfs_in_folder,
    _load_log,
    _log_path_for,
    _web_log_path,
    EXPORT_SUBFOLDER,
    LOG_FILENAME,
)

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# PDF — text
# ---------------------------------------------------------------------------

class TestE2ePdfText:
    def test_converts_text_pdf(self, sample_text_pdf, tmp_path):
        pdf_path = str(sample_text_pdf)
        result = convert_pdf_to_markdown(pdf_path)

        assert "Converted successfully" in result

        out_dir = sample_text_pdf.parent / EXPORT_SUBFOLDER
        md_files = list(out_dir.glob("*.md"))
        assert len(md_files) == 1

        content = md_files[0].read_text(encoding="utf-8")
        assert "Chapter 1" in content or "Introduction" in content
        assert "Chapter 2" in content or "Details" in content

        log = _load_log(_log_path_for(pdf_path))
        key = os.path.normpath(pdf_path)
        assert key in log
        assert log[key]["status"] == "ok"
        assert log[key].get("pages") == 2

    def test_skip_on_repeat(self, sample_text_pdf):
        pdf_path = str(sample_text_pdf)
        convert_pdf_to_markdown(pdf_path)
        result2 = convert_pdf_to_markdown(pdf_path)

        assert "Skipped" in result2

        log = _load_log(_log_path_for(pdf_path))
        key = os.path.normpath(pdf_path)
        assert log[key].get("skip_count", 0) >= 1
        assert "last_checked_at" in log[key]

    def test_force_reconvert(self, sample_text_pdf):
        pdf_path = str(sample_text_pdf)
        convert_pdf_to_markdown(pdf_path)
        result = convert_pdf_to_markdown(pdf_path, force=True)
        assert "Converted successfully" in result


# ---------------------------------------------------------------------------
# PDF — with image (OCR pipeline, no easyocr)
# ---------------------------------------------------------------------------

class TestE2ePdfImage:
    def test_image_pdf_ocr_pipeline(self, sample_image_pdf):
        """Test that OCR pipeline runs without crash. We mock easyocr to avoid
        slow model download, but everything else is real."""
        from unittest.mock import patch

        pdf_path = str(sample_image_pdf)

        with patch("server._ocr_image_file", return_value="mocked ocr text"):
            result = convert_pdf_to_markdown(pdf_path, ocr="always")

        assert "Converted successfully" in result
        assert "OCR" in result

        log = _load_log(_log_path_for(pdf_path))
        key = os.path.normpath(pdf_path)
        assert log[key]["status"] == "ok"
        assert log[key].get("ocr") is True

    @pytest.mark.slow
    def test_image_pdf_real_ocr(self, sample_image_pdf):
        """Full OCR with real easyocr. Slow on first run (model download)."""
        pdf_path = str(sample_image_pdf)
        result = convert_pdf_to_markdown(pdf_path, ocr="always", ocr_languages="en")
        assert "Converted successfully" in result

        log = _load_log(_log_path_for(pdf_path))
        key = os.path.normpath(pdf_path)
        assert log[key]["status"] == "ok"
        assert log[key].get("ocr") is True


# ---------------------------------------------------------------------------
# Swagger YAML
# ---------------------------------------------------------------------------

class TestE2eSwaggerYaml:
    def test_converts_yaml(self, sample_swagger_yaml):
        result = convert_swagger_to_markdown(str(sample_swagger_yaml))

        assert "Converted successfully" in result
        assert "Endpoints: 2" in result
        assert "Models: 1" in result

        out_dir = sample_swagger_yaml.parent / EXPORT_SUBFOLDER
        md_files = list(out_dir.glob("*.md"))
        assert len(md_files) == 1

        content = md_files[0].read_text(encoding="utf-8")
        assert "Test Pet API" in content
        assert "GET /pets" in content
        assert "GET /pets/{petId}" in content
        assert "Pet" in content
        assert "Models" in content

    def test_log_metadata(self, sample_swagger_yaml):
        convert_swagger_to_markdown(str(sample_swagger_yaml))

        log = _load_log(_log_path_for(str(sample_swagger_yaml)))
        key = os.path.normpath(str(sample_swagger_yaml))
        assert key in log
        entry = log[key]
        assert entry["status"] == "ok"
        assert entry.get("endpoints") == 2
        assert entry.get("models") == 1


# ---------------------------------------------------------------------------
# Swagger JSON
# ---------------------------------------------------------------------------

class TestE2eSwaggerJson:
    def test_converts_json(self, sample_swagger_json):
        result = convert_swagger_to_markdown(str(sample_swagger_json))

        assert "Converted successfully" in result

        out_dir = sample_swagger_json.parent / EXPORT_SUBFOLDER
        md_files = list(out_dir.glob("*.md"))
        assert len(md_files) == 1

        content = md_files[0].read_text(encoding="utf-8")
        assert "Test Pet API v2" in content
        assert "GET /pets" in content
        assert "Pet" in content


# ---------------------------------------------------------------------------
# HTTP — direct OpenAPI spec
# ---------------------------------------------------------------------------

class TestE2eHttpOpenapi:
    @pytest.mark.asyncio
    async def test_direct_spec(self, local_http_server, tmp_path):
        url = f"{local_http_server}/openapi.yaml"
        result = await convert_api_url_to_markdown(url, output_dir=str(tmp_path))

        assert "Converted successfully" in result
        assert "Direct OpenAPI spec" in result

        log = _load_log(_web_log_path(str(tmp_path)))
        entry = log.get(url, {})
        assert entry.get("status") == "ok"
        assert entry.get("detection") == "direct_openapi_spec"

        out_dir = tmp_path / EXPORT_SUBFOLDER
        md_files = list(out_dir.glob("*.md"))
        assert len(md_files) >= 1
        content = md_files[0].read_text(encoding="utf-8")
        assert "Test Pet API" in content

    @pytest.mark.asyncio
    async def test_json_spec(self, local_http_server, tmp_path):
        url = f"{local_http_server}/openapi.json"
        result = await convert_api_url_to_markdown(url, output_dir=str(tmp_path))

        assert "Converted successfully" in result
        assert "Direct OpenAPI spec" in result


# ---------------------------------------------------------------------------
# HTTP — Swagger UI extraction
# ---------------------------------------------------------------------------

class TestE2eHttpSwaggerUi:
    @pytest.mark.asyncio
    async def test_swagger_ui_page(self, local_http_server, tmp_path):
        url = f"{local_http_server}/swagger_ui.html"
        result = await convert_api_url_to_markdown(url, output_dir=str(tmp_path))

        assert "Converted successfully" in result
        assert "Extracted from Swagger UI" in result

        log = _load_log(_web_log_path(str(tmp_path)))
        entry = log.get(url, {})
        assert entry.get("detection") == "swagger_ui_extracted"


# ---------------------------------------------------------------------------
# Batch PDF conversion
# ---------------------------------------------------------------------------

class TestE2eBatchPdfs:
    @pytest.mark.asyncio
    async def test_batch_convert(self, tmp_path):
        pdf_dir = tmp_path / "pdfs"
        pdf_dir.mkdir()

        for name in ["alpha", "beta", "gamma"]:
            doc = pymupdf.open()
            page = doc.new_page()
            page.insert_text((72, 72), f"Document: {name}\n\nContent of {name}.")
            doc.save(str(pdf_dir / f"{name}.pdf"))
            doc.close()

        result = await convert_all_pdfs_in_folder(str(pdf_dir))
        assert "Converted: 3" in result

        export = pdf_dir / EXPORT_SUBFOLDER
        md_files = sorted(export.glob("*.md"))
        assert len(md_files) == 3

        for md_file in md_files:
            content = md_file.read_text(encoding="utf-8")
            assert len(content) > 0

        result2 = await convert_all_pdfs_in_folder(str(pdf_dir))
        assert "Skipped: 3" in result2
