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
        """Generate a 2-page PDF with known text, convert to MD,
        verify all text is present and log is correct."""
        pdf_path = str(sample_text_pdf)
        result = convert_pdf_to_markdown(pdf_path)

        assert "Converted successfully" in result

        out_dir = sample_text_pdf.parent / EXPORT_SUBFOLDER
        md_files = list(out_dir.glob("*.md"))
        assert len(md_files) == 1

        content = md_files[0].read_text(encoding="utf-8")
        for phrase in [
            "Chapter 1", "Introduction", "test document", "animals",
            "Chapter 2", "Details", "Cats and dogs", "popular pets",
        ]:
            assert phrase in content, f"Expected '{phrase}' in converted MD"

        assert len(content.strip()) > 100, "MD output should be non-trivial"

        log = _load_log(_log_path_for(pdf_path))
        key = os.path.normpath(pdf_path)
        assert key in log
        entry = log[key]
        assert entry["status"] == "ok"
        assert entry.get("pages") == 2
        assert entry.get("chars", 0) > 100
        assert entry.get("lines", 0) > 1
        assert "converted_at" in entry
        assert "duration_sec" in entry

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
        assert "images_total" in log[key]
        assert "images_ocr_ok" in log[key]
        assert "images_failed" in log[key]

    def test_recognized_images_deleted(self, sample_image_pdf):
        """Images that were successfully OCR'd must be deleted from disk."""
        from unittest.mock import patch

        pdf_path = str(sample_image_pdf)
        export_dir = sample_image_pdf.parent / EXPORT_SUBFOLDER

        with patch("server._ocr_image_file", return_value="recognized text"):
            convert_pdf_to_markdown(pdf_path, ocr="always")

        png_files = list(export_dir.glob("*.png"))
        assert png_files == [], f"Expected no PNGs after OCR, found: {[f.name for f in png_files]}"

    def test_unrecognized_images_deleted(self, sample_image_pdf):
        """Images where OCR returned empty text must also be deleted from disk."""
        from unittest.mock import patch

        pdf_path = str(sample_image_pdf)
        export_dir = sample_image_pdf.parent / EXPORT_SUBFOLDER

        with patch("server._ocr_image_file", return_value=""):
            convert_pdf_to_markdown(pdf_path, ocr="always")

        png_files = list(export_dir.glob("*.png"))
        assert png_files == [], f"Expected no PNGs after OCR, found: {[f.name for f in png_files]}"

    def test_ocr_error_images_deleted(self, sample_image_pdf):
        """Images where OCR raised an exception must also be deleted from disk."""
        from unittest.mock import patch

        pdf_path = str(sample_image_pdf)
        export_dir = sample_image_pdf.parent / EXPORT_SUBFOLDER

        with patch("server._ocr_image_file", side_effect=RuntimeError("OCR crash")):
            convert_pdf_to_markdown(pdf_path, ocr="always")

        png_files = list(export_dir.glob("*.png"))
        assert png_files == [], f"Expected no PNGs after OCR, found: {[f.name for f in png_files]}"

    def test_unrecognized_images_logged(self, sample_image_pdf):
        """Failed images must be recorded in conversion log with stats."""
        from unittest.mock import patch

        pdf_path = str(sample_image_pdf)

        with patch("server._ocr_image_file", return_value=""):
            convert_pdf_to_markdown(pdf_path, ocr="always")

        log = _load_log(_log_path_for(pdf_path))
        key = os.path.normpath(pdf_path)
        entry = log[key]

        assert entry["status"] == "ok"
        assert entry["ocr"] is True
        assert entry["images_total"] >= 1
        assert entry["images_ocr_ok"] == 0
        assert entry["images_ocr_empty"] >= 1
        assert len(entry["images_failed"]) >= 1
        for name in entry["images_failed"]:
            assert name.endswith(".png")

    def test_mixed_ocr_results_logged(self, tmp_path):
        """PDF with multiple images: some recognized, some not — log has full stats."""
        from unittest.mock import patch

        pdf_path = tmp_path / "multi.pdf"
        doc = pymupdf.open()

        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 50), 1)
        pix.set_rect(pix.irect, (255, 255, 255, 255))
        img_bytes = pix.tobytes("png")

        for i in range(3):
            page = doc.new_page()
            page.insert_image(pymupdf.Rect(72, 72, 272, 122), stream=img_bytes)
        doc.save(str(pdf_path))
        doc.close()

        call_idx = 0
        def mock_ocr(path, langs=None):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                return "good text"
            if call_idx == 2:
                return "   "
            raise RuntimeError("crash")

        with patch("server._ocr_image_file", side_effect=mock_ocr):
            result = convert_pdf_to_markdown(str(pdf_path), ocr="always")

        assert "Converted successfully" in result

        log = _load_log(_log_path_for(str(pdf_path)))
        key = os.path.normpath(str(pdf_path))
        entry = log[key]

        assert entry["images_ocr_ok"] >= 1
        assert entry["images_ocr_empty"] >= 1
        assert entry["images_ocr_error"] >= 1
        assert len(entry["images_failed"]) >= 2
        assert entry["images_total"] == entry["images_ocr_ok"] + entry["images_ocr_empty"] + entry["images_ocr_error"] + entry.get("images_missing", 0)

        export_dir = tmp_path / EXPORT_SUBFOLDER
        png_files = list(export_dir.glob("*.png"))
        assert png_files == [], f"All PNGs must be deleted, found: {[f.name for f in png_files]}"

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

    @pytest.mark.slow
    def test_ocr_recognizes_known_text(self, sample_ocr_pdf):
        """PDF with Pillow-rendered 'HELLO WORLD' image: real OCR must find it in output MD."""
        pdf_path, expected_text = sample_ocr_pdf
        pdf_path_str = str(pdf_path)

        result = convert_pdf_to_markdown(pdf_path_str, ocr="always", ocr_languages="en")
        assert "Converted successfully" in result

        export_dir = pdf_path.parent / EXPORT_SUBFOLDER
        md_files = list(export_dir.glob("*.md"))
        assert len(md_files) == 1

        content = md_files[0].read_text(encoding="utf-8")
        assert "Chapter 1" in content, "Regular text from page 1 must be present"

        content_upper = content.upper()
        assert "HELLO" in content_upper and "WORLD" in content_upper, (
            f"OCR must recognize 'HELLO WORLD' from the image. MD content:\n{content[:500]}"
        )

        log = _load_log(_log_path_for(pdf_path_str))
        key = os.path.normpath(pdf_path_str)
        entry = log[key]
        assert entry["status"] == "ok"
        assert entry["ocr"] is True
        assert entry["images_ocr_ok"] >= 1, "At least one image must be successfully OCR'd"

        png_files = list(export_dir.glob("*.png"))
        assert png_files == [], f"All PNGs must be cleaned up, found: {[f.name for f in png_files]}"


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
