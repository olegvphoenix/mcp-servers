"""Unit tests for MCP tool functions (with mocked heavy dependencies)."""

import json
import os
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from server import (
    convert_pdf_to_markdown,
    convert_swagger_to_markdown,
    convert_api_url_to_markdown,
    _load_log,
    _log_path_for,
    _web_log_path,
)


# ---------------------------------------------------------------------------
# convert_pdf_to_markdown
# ---------------------------------------------------------------------------

class TestConvertPdfToMarkdown:
    @pytest.mark.asyncio
    async def test_file_not_found(self):
        result = await convert_pdf_to_markdown("/nonexistent/file.pdf")
        assert "Error: file not found" in result

    @pytest.mark.asyncio
    async def test_successful_conversion(self, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake content")

        with patch("server.pymupdf4llm.to_markdown", return_value="# Hello\n\nWorld"), \
             patch("server._find_ocr_pages", return_value=[]), \
             patch("server._pdf_metadata", return_value={"pages": 1}):
            result = await convert_pdf_to_markdown(str(pdf))

        assert "Converted successfully" in result
        out_path = str(tmp_path / "doc2md_export" / "test.md")
        assert os.path.isfile(out_path)
        content = open(out_path, encoding="utf-8").read()
        assert content == "# Hello\n\nWorld"

        log = _load_log(_log_path_for(str(pdf)))
        key = os.path.normpath(str(pdf))
        assert key in log
        assert log[key]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_skip_already_converted(self, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake content")

        with patch("server.pymupdf4llm.to_markdown", return_value="# Hello"), \
             patch("server._find_ocr_pages", return_value=[]), \
             patch("server._pdf_metadata", return_value={}):
            await convert_pdf_to_markdown(str(pdf))
            result2 = await convert_pdf_to_markdown(str(pdf))

        assert "Skipped" in result2
        log = _load_log(_log_path_for(str(pdf)))
        key = os.path.normpath(str(pdf))
        assert log[key].get("skip_count", 0) >= 1

    @pytest.mark.asyncio
    async def test_force_reconvert(self, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake content")

        with patch("server.pymupdf4llm.to_markdown", return_value="# V1"), \
             patch("server._find_ocr_pages", return_value=[]), \
             patch("server._pdf_metadata", return_value={}):
            await convert_pdf_to_markdown(str(pdf))

        with patch("server.pymupdf4llm.to_markdown", return_value="# V2"), \
             patch("server._find_ocr_pages", return_value=[]), \
             patch("server._pdf_metadata", return_value={}):
            result = await convert_pdf_to_markdown(str(pdf), force=True)

        assert "Converted successfully" in result
        out_path = str(tmp_path / "doc2md_export" / "test.md")
        content = open(out_path, encoding="utf-8").read()
        assert content == "# V2"


# ---------------------------------------------------------------------------
# convert_swagger_to_markdown
# ---------------------------------------------------------------------------

class TestConvertSwaggerToMarkdown:
    def test_file_not_found(self):
        result = convert_swagger_to_markdown("/nonexistent/swagger.yaml")
        assert "Error: file not found" in result

    def test_successful_conversion(self, sample_swagger_yaml):
        result = convert_swagger_to_markdown(str(sample_swagger_yaml))
        assert "Converted successfully" in result
        assert "Endpoints: 2" in result
        assert "Models: 1" in result

        out_path = str(sample_swagger_yaml.parent / "doc2md_export" / "openapi.md")
        assert os.path.isfile(out_path)
        content = open(out_path, encoding="utf-8").read()
        assert "Test Pet API" in content

    def test_skip_already_converted(self, sample_swagger_yaml):
        convert_swagger_to_markdown(str(sample_swagger_yaml))
        result2 = convert_swagger_to_markdown(str(sample_swagger_yaml))
        assert "Skipped" in result2


# ---------------------------------------------------------------------------
# convert_api_url_to_markdown (async)
# ---------------------------------------------------------------------------

class TestConvertApiUrlToMarkdown:
    @pytest.mark.asyncio
    async def test_invalid_url(self):
        result = await convert_api_url_to_markdown("not-a-url")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_direct_openapi_spec(self, tmp_path):
        spec_json = json.dumps({"openapi": "3.0.0", "info": {"title": "Remote API", "version": "1.0"}, "paths": {}})

        with patch("server._fetch_url", return_value=(spec_json.encode(), "application/json", "http://x.com/spec.json")):
            result = await convert_api_url_to_markdown(
                "http://x.com/spec.json",
                output_dir=str(tmp_path),
            )

        assert "Converted successfully" in result
        assert "Direct OpenAPI spec" in result

        log = _load_log(_web_log_path(str(tmp_path)))
        entry = log.get("http://x.com/spec.json", {})
        assert entry.get("detection") == "direct_openapi_spec"

    @pytest.mark.asyncio
    async def test_swagger_ui_extracted(self, tmp_path):
        html = b"""
        <html><body>
        <script>SwaggerUIBundle({url: "/openapi.json"})</script>
        </body></html>
        """
        spec_json = json.dumps({"openapi": "3.0.0", "info": {"title": "Extracted API", "version": "1.0"}, "paths": {}})

        call_count = 0
        def mock_fetch(url, accept="*/*"):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (html, "text/html", "http://x.com/docs")
            return (spec_json.encode(), "application/json", url)

        with patch("server._fetch_url", side_effect=mock_fetch):
            result = await convert_api_url_to_markdown(
                "http://x.com/docs",
                output_dir=str(tmp_path),
            )

        assert "Converted successfully" in result
        assert "Extracted from Swagger UI" in result

    @pytest.mark.asyncio
    async def test_crawl4ai_fallback(self, tmp_path):
        html = b"<html><body><p>Regular page</p></body></html>"

        with patch("server._fetch_url", return_value=(html, "text/html", "http://x.com/page")), \
             patch("server._crawl_url", new_callable=AsyncMock, return_value=("# Crawled Content", "Page Title")):
            result = await convert_api_url_to_markdown(
                "http://x.com/page",
                output_dir=str(tmp_path),
            )

        assert "Converted successfully" in result
        assert "Web page (Crawl4AI)" in result
