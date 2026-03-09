"""Tests for OCR enrichment logic (_enrich_markdown_with_ocr, _ocr_image_file)."""

import os
from unittest.mock import patch, MagicMock

import pytest

from server import (
    _enrich_markdown_with_ocr,
    _find_ocr_pages,
    _IMG_REF_RE,
    _OCR_IMAGE_MIN_AREA,
    _ocr_image_file,
)


# ---------------------------------------------------------------------------
# _IMG_REF_RE — regex correctness
# ---------------------------------------------------------------------------

class TestImgRefRegex:
    """Verify that _IMG_REF_RE correctly handles edge-case paths."""

    def test_simple_path(self):
        md = "![alt](images/photo.png)"
        m = _IMG_REF_RE.search(md)
        assert m is not None
        assert m.group(2) == "images/photo.png"

    def test_path_with_single_parens(self):
        md = "![](D:/docs/Guide_V2.6.1(2)/img.png)"
        m = _IMG_REF_RE.search(md)
        assert m is not None
        assert m.group(2) == "D:/docs/Guide_V2.6.1(2)/img.png"

    def test_path_with_multiple_parens(self):
        md = "![](D:/export/Name (copy)/V2.6.1(2)-file.pdf-0-full.png)"
        m = _IMG_REF_RE.search(md)
        assert m is not None
        assert m.group(2) == "D:/export/Name (copy)/V2.6.1(2)-file.pdf-0-full.png"

    def test_path_with_spaces_and_parens(self):
        md = "![x](C:/My Folder (1)/sub dir (test)/image(3).png)"
        m = _IMG_REF_RE.search(md)
        assert m is not None
        assert m.group(2) == "C:/My Folder (1)/sub dir (test)/image(3).png"

    def test_multiple_refs_same_line(self):
        md = "![a](dir(1)/a.png) text ![b](dir(2)/b.png)"
        matches = _IMG_REF_RE.findall(md)
        assert len(matches) == 2
        assert matches[0][1] == "dir(1)/a.png"
        assert matches[1][1] == "dir(2)/b.png"

    def test_no_parens_in_path(self):
        md = "![](simple/path/image.png)"
        m = _IMG_REF_RE.search(md)
        assert m is not None
        assert m.group(2) == "simple/path/image.png"

    def test_empty_alt_text(self):
        md = "![](path(x)/img.png)"
        m = _IMG_REF_RE.search(md)
        assert m is not None
        assert m.group(1) == ""
        assert m.group(2) == "path(x)/img.png"


# ---------------------------------------------------------------------------
# _ocr_image_file — PIL-based reading (non-ASCII path support)
# ---------------------------------------------------------------------------

class TestOcrImageFile:
    """_ocr_image_file reads images via PIL to avoid OpenCV path issues."""

    def _make_white_png(self, path):
        from PIL import Image
        img = Image.new("RGB", (100, 50), (255, 255, 255))
        img.save(str(path))

    def test_non_ascii_path(self, tmp_path):
        """Paths with non-breaking spaces / Unicode must not crash OpenCV."""
        subdir = tmp_path / "C#\xa0Access\xa0Demo"
        subdir.mkdir()
        img = subdir / "test.png"
        self._make_white_png(img)

        with patch("server._get_ocr_reader") as mock_reader:
            mock_reader.return_value.readtext.return_value = [
                (None, "hello", 0.9),
            ]
            result = _ocr_image_file(str(img), ["en"])

        assert result == "hello"

    def test_path_with_parentheses(self, tmp_path):
        subdir = tmp_path / "Guide(v2)"
        subdir.mkdir()
        img = subdir / "img(0).png"
        self._make_white_png(img)

        with patch("server._get_ocr_reader") as mock_reader:
            mock_reader.return_value.readtext.return_value = [
                (None, "world", 0.95),
            ]
            result = _ocr_image_file(str(img), ["en"])

        assert result == "world"

    def test_path_with_spaces(self, tmp_path):
        subdir = tmp_path / "My Documents"
        subdir.mkdir()
        img = subdir / "photo.png"
        self._make_white_png(img)

        with patch("server._get_ocr_reader") as mock_reader:
            mock_reader.return_value.readtext.return_value = []
            result = _ocr_image_file(str(img), ["en"])

        assert result == ""


# ---------------------------------------------------------------------------
# _enrich_markdown_with_ocr
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# _find_ocr_pages — page-level OCR detection by image area
# ---------------------------------------------------------------------------

class TestFindOcrPages:
    """_find_ocr_pages returns page indices with large images."""

    def test_returns_pages_with_large_images(self, tmp_path):
        """Pages containing images >= _OCR_IMAGE_MIN_AREA should be returned."""
        import pymupdf

        pdf_path = str(tmp_path / "test.pdf")
        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)
        from PIL import Image
        import io
        img = Image.new("RGB", (400, 400), (128, 128, 128))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        rect = pymupdf.Rect(50, 50, 450, 450)
        page.insert_image(rect, stream=buf.read())
        doc.save(pdf_path)
        doc.close()

        result = _find_ocr_pages(pdf_path)
        assert result == [0], f"Expected [0] for 400x400 image (160000 px), got {result}"

    def test_skips_pages_with_small_images(self, tmp_path):
        """Pages with only small images (< _OCR_IMAGE_MIN_AREA) should be skipped."""
        import pymupdf

        pdf_path = str(tmp_path / "test.pdf")
        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)
        from PIL import Image
        import io
        img = Image.new("RGB", (50, 50), (200, 200, 200))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        rect = pymupdf.Rect(50, 50, 100, 100)
        page.insert_image(rect, stream=buf.read())
        doc.save(pdf_path)
        doc.close()

        result = _find_ocr_pages(pdf_path)
        assert result == [], f"Expected [] for 50x50 image (2500 px), got {result}"

    def test_empty_pdf(self, tmp_path):
        """PDF with no images returns empty list."""
        import pymupdf

        pdf_path = str(tmp_path / "empty.pdf")
        doc = pymupdf.open()
        doc.new_page(width=612, height=792)
        doc.save(pdf_path)
        doc.close()

        result = _find_ocr_pages(pdf_path)
        assert result == []

    def test_mixed_pages(self, tmp_path):
        """Only pages with large images are returned."""
        import pymupdf

        pdf_path = str(tmp_path / "mixed.pdf")
        doc = pymupdf.open()
        from PIL import Image
        import io

        page0 = doc.new_page(width=612, height=792)

        page1 = doc.new_page(width=612, height=792)
        img_large = Image.new("RGB", (500, 500), (100, 100, 100))
        buf = io.BytesIO()
        img_large.save(buf, format="PNG")
        buf.seek(0)
        page1.insert_image(pymupdf.Rect(10, 10, 510, 510), stream=buf.read())

        page2 = doc.new_page(width=612, height=792)
        img_small = Image.new("RGB", (20, 20), (50, 50, 50))
        buf2 = io.BytesIO()
        img_small.save(buf2, format="PNG")
        buf2.seek(0)
        page2.insert_image(pymupdf.Rect(10, 10, 30, 30), stream=buf2.read())

        doc.save(pdf_path)
        doc.close()

        result = _find_ocr_pages(pdf_path)
        assert result == [1], f"Expected [1] (only page with 500x500 image), got {result}"


# ---------------------------------------------------------------------------
# _enrich_markdown_with_ocr
# ---------------------------------------------------------------------------

class TestEnrichMarkdownWithOcr:
    def test_replaces_image_with_ocr_text(self, tmp_path):
        img = tmp_path / "diagram.png"
        img.touch()
        md = f"Before\n![alt]({img})\nAfter"

        with patch("server._ocr_image_file", return_value="recognized text"):
            result, stats = _enrich_markdown_with_ocr(md, ["en"])

        assert "recognized text" in result
        assert "![alt]" not in result
        assert stats["images_ocr_ok"] == 1
        assert stats["images_total"] == 1
        assert stats["images_failed"] == []
        assert len(stats["images_ok_paths"]) == 1
        assert str(img) in stats["images_ok_paths"][0]

    def test_file_not_found(self):
        md = "![alt](/nonexistent/image.png)"
        result, stats = _enrich_markdown_with_ocr(md, ["en"])
        assert "![alt]" not in result
        assert result.strip() == ""
        assert stats["images_ocr_ok"] == 0
        assert stats["images_missing"] == 1
        assert len(stats["images_failed"]) == 1
        assert len(stats["errors_detail"]) == 1
        ed = stats["errors_detail"][0]
        assert ed["reason"] == "missing"
        assert ed["file"] == "image.png"
        assert "/nonexistent/image.png" in ed["detail"]

    def test_empty_ocr_result(self, tmp_path):
        img = tmp_path / "empty.png"
        img.touch()
        md = f"Text\n![x]({img})\nMore"

        with patch("server._ocr_image_file", return_value="   "):
            result, stats = _enrich_markdown_with_ocr(md, ["en"])

        assert "![x]" not in result
        assert stats["images_ocr_ok"] == 0
        assert stats["images_ocr_empty"] == 1
        assert "empty.png" in stats["images_failed"]
        assert len(stats["errors_detail"]) == 1
        ed = stats["errors_detail"][0]
        assert ed["reason"] == "ocr_empty"
        assert ed["file"] == "empty.png"

    def test_ocr_exception(self, tmp_path):
        img = tmp_path / "bad.png"
        img.touch()
        md = f"![x]({img})"

        with patch("server._ocr_image_file", side_effect=RuntimeError("OCR crashed")):
            result, stats = _enrich_markdown_with_ocr(md, ["en"])

        assert "![x]" not in result
        assert stats["images_ocr_ok"] == 0
        assert stats["images_ocr_error"] == 1
        assert "bad.png" in stats["images_failed"]
        assert len(stats["errors_detail"]) == 1
        ed = stats["errors_detail"][0]
        assert ed["reason"] == "ocr_error"
        assert ed["file"] == "bad.png"
        assert "RuntimeError" in ed["detail"]
        assert "OCR crashed" in ed["detail"]

    def test_no_images(self):
        md = "Just plain text\nwith no images"
        result, stats = _enrich_markdown_with_ocr(md, ["en"])
        assert result == md
        assert stats["images_total"] == 0
        assert stats["images_failed"] == []

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
            result, stats = _enrich_markdown_with_ocr(md, ["en"])

        assert "alpha text" in result
        assert "beta text" in result
        assert stats["images_ocr_ok"] == 2
        assert stats["images_total"] == 2

    def test_path_with_parentheses(self, tmp_path):
        """Image path containing parentheses must be parsed and OCR'd correctly."""
        subdir = tmp_path / "Guide_V2.6.1(2)"
        subdir.mkdir()
        img = subdir / "image(0).png"
        img.touch()
        md = f"Text\n![alt]({img})\nEnd"

        with patch("server._ocr_image_file", return_value="found text"):
            result, stats = _enrich_markdown_with_ocr(md, ["en"])

        assert "found text" in result
        assert "![alt]" not in result
        assert stats["images_total"] == 1
        assert stats["images_ocr_ok"] == 1
        assert stats["images_missing"] == 0

    def test_mixed_results(self, tmp_path):
        """One image recognized, one empty, one error."""
        img_ok = tmp_path / "ok.png"
        img_empty = tmp_path / "empty.png"
        img_err = tmp_path / "err.png"
        img_ok.touch()
        img_empty.touch()
        img_err.touch()
        md = f"![a]({img_ok})\n![b]({img_empty})\n![c]({img_err})\n![d](/missing.png)"

        def mock_ocr(path, langs=None):
            if "ok.png" in path:
                return "good text"
            if "empty.png" in path:
                return "  "
            raise RuntimeError("crash")

        with patch("server._ocr_image_file", side_effect=mock_ocr):
            result, stats = _enrich_markdown_with_ocr(md, ["en"])

        assert stats["images_total"] == 4
        assert stats["images_ocr_ok"] == 1
        assert stats["images_ocr_empty"] == 1
        assert stats["images_ocr_error"] == 1
        assert stats["images_missing"] == 1
        assert len(stats["images_failed"]) == 3
        assert len(stats["images_ok_paths"]) == 1
        assert "good text" in result
        assert len(stats["errors_detail"]) == 3
        reasons = {ed["reason"] for ed in stats["errors_detail"]}
        assert reasons == {"ocr_empty", "ocr_error", "missing"}

    def test_skips_small_images(self, tmp_path):
        """Images smaller than _OCR_IMAGE_MIN_AREA are skipped without OCR."""
        from PIL import Image

        small_img = tmp_path / "tiny.png"
        Image.new("RGB", (20, 20), (255, 255, 255)).save(str(small_img))

        large_img = tmp_path / "big.png"
        Image.new("RGB", (400, 400), (128, 128, 128)).save(str(large_img))

        md = f"![small]({small_img})\n![big]({large_img})"

        with patch("server._ocr_image_file", return_value="big text"):
            result, stats = _enrich_markdown_with_ocr(md, ["en"])

        assert stats["images_total"] == 2
        assert stats["images_skipped_small"] == 1
        assert stats["images_ocr_ok"] == 1
        assert "big text" in result
        assert "![small]" not in result
        assert "![big]" not in result

    def test_all_small_images_skipped(self, tmp_path):
        """When all images are small, none are OCR'd."""
        from PIL import Image

        img1 = tmp_path / "icon1.png"
        img2 = tmp_path / "icon2.png"
        Image.new("RGB", (10, 10), (255, 0, 0)).save(str(img1))
        Image.new("RGB", (30, 30), (0, 255, 0)).save(str(img2))

        md = f"Text\n![a]({img1})\n![b]({img2})\nEnd"

        with patch("server._ocr_image_file") as mock_ocr:
            result, stats = _enrich_markdown_with_ocr(md, ["en"])

        mock_ocr.assert_not_called()
        assert stats["images_total"] == 2
        assert stats["images_skipped_small"] == 2
        assert stats["images_ocr_ok"] == 0

    def test_on_progress_called_with_correct_sequence(self, tmp_path):
        """on_progress must be called: (0, N) for model init, then (1, N)...(N, N)."""
        img1 = tmp_path / "a.png"
        img2 = tmp_path / "b.png"
        img3 = tmp_path / "c.png"
        for img in [img1, img2, img3]:
            img.touch()
        md = f"![a]({img1})\n![b]({img2})\n![c]({img3})"

        calls: list[tuple[int, int]] = []

        with patch("server._ocr_image_file", return_value="text"), \
             patch("server._get_ocr_reader"):
            _enrich_markdown_with_ocr(md, ["en"], on_progress=lambda d, t: calls.append((d, t)))

        assert calls[0] == (0, 3), f"First call must be (0, 3) for model loading, got {calls[0]}"
        assert calls == [(0, 3), (1, 3), (2, 3), (3, 3)]

    def test_on_progress_not_called_without_images(self):
        """on_progress must not be called when there are no images."""
        calls: list = []
        md = "Just plain text"

        _enrich_markdown_with_ocr(md, ["en"], on_progress=lambda d, t: calls.append((d, t)))

        assert calls == [], f"No images = no progress calls, got {calls}"

    def test_on_progress_none_is_safe(self, tmp_path):
        """on_progress=None must not cause errors."""
        img = tmp_path / "x.png"
        img.touch()
        md = f"![x]({img})"

        with patch("server._ocr_image_file", return_value="ok"), \
             patch("server._get_ocr_reader"):
            result, stats = _enrich_markdown_with_ocr(md, ["en"], on_progress=None)

        assert stats["images_ocr_ok"] == 1
