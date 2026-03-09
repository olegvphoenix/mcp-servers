"""Tests for OCR enrichment logic (_enrich_markdown_with_ocr)."""

import os
from unittest.mock import patch

import pytest

from server import _enrich_markdown_with_ocr


class TestEnrichMarkdownWithOcr:
    def test_replaces_image_with_ocr_text(self, tmp_path):
        img = tmp_path / "diagram.png"
        img.touch()
        md = f"Before\n![alt]({img})\nAfter"

        with patch("server._ocr_image_file", return_value="recognized text"):
            result, count = _enrich_markdown_with_ocr(md, ["en"])

        assert "recognized text" in result
        assert "![alt]" not in result
        assert count == 1

    def test_file_not_found(self):
        md = "![alt](/nonexistent/image.png)"
        result, count = _enrich_markdown_with_ocr(md, ["en"])
        assert "![alt]" not in result
        assert result.strip() == ""
        assert count == 0

    def test_empty_ocr_result(self, tmp_path):
        img = tmp_path / "empty.png"
        img.touch()
        md = f"Text\n![x]({img})\nMore"

        with patch("server._ocr_image_file", return_value="   "):
            result, count = _enrich_markdown_with_ocr(md, ["en"])

        assert "![x]" not in result
        assert count == 0

    def test_ocr_exception(self, tmp_path):
        img = tmp_path / "bad.png"
        img.touch()
        md = f"![x]({img})"

        with patch("server._ocr_image_file", side_effect=RuntimeError("OCR crashed")):
            result, count = _enrich_markdown_with_ocr(md, ["en"])

        assert "![x]" not in result
        assert count == 0

    def test_no_images(self):
        md = "Just plain text\nwith no images"
        result, count = _enrich_markdown_with_ocr(md, ["en"])
        assert result == md
        assert count == 0

    def test_multiple_images(self, tmp_path):
        img1 = tmp_path / "a.png"
        img2 = tmp_path / "b.png"
        img1.touch()
        img2.touch()
        md = f"![first]({img1})\nMiddle\n![second]({img2})"

        def mock_ocr(path, langs=None):
            if "a.png" in path:
                return "alpha text"
            return "beta text"

        with patch("server._ocr_image_file", side_effect=mock_ocr):
            result, count = _enrich_markdown_with_ocr(md, ["en"])

        assert "alpha text" in result
        assert "beta text" in result
        assert count == 2
