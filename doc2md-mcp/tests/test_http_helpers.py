"""Tests for HTTP API helpers and web page helpers."""

import hashlib
import json
from io import BytesIO
from unittest.mock import patch, MagicMock

import pytest

from server import (
    _fetch_url,
    _try_parse_as_openapi,
    _detect_swagger_spec_url,
    _url_hash,
    _url_to_filename,
    _resolve_web_output_path,
    _detect_wait_for,
    _POSTMAN_DOMAIN,
    EXPORT_SUBFOLDER,
)


# ---------------------------------------------------------------------------
# _try_parse_as_openapi
# ---------------------------------------------------------------------------

class TestTryParseAsOpenapi:
    def test_json_swagger(self):
        body = json.dumps({"swagger": "2.0", "info": {"title": "T"}}).encode()
        result = _try_parse_as_openapi(body, "application/json")
        assert result is not None
        assert result["swagger"] == "2.0"

    def test_json_openapi(self):
        body = json.dumps({"openapi": "3.0.0", "info": {"title": "T"}}).encode()
        result = _try_parse_as_openapi(body, "application/json")
        assert result is not None
        assert result["openapi"] == "3.0.0"

    def test_yaml_openapi(self):
        body = b"openapi: '3.0.0'\ninfo:\n  title: T\n"
        result = _try_parse_as_openapi(body, "text/yaml")
        assert result is not None

    def test_html_returns_none(self):
        body = b"<html><body>Hello</body></html>"
        result = _try_parse_as_openapi(body, "text/html")
        assert result is None

    def test_broken_json(self):
        body = b"{broken json"
        result = _try_parse_as_openapi(body, "application/json")
        assert result is None

    def test_plain_dict_no_swagger_key(self):
        body = json.dumps({"name": "test"}).encode()
        result = _try_parse_as_openapi(body, "application/json")
        assert result is None


# ---------------------------------------------------------------------------
# _detect_swagger_spec_url
# ---------------------------------------------------------------------------

class TestDetectSwaggerSpecUrl:
    def test_swagger_ui_bundle(self):
        html = """
        <script>
        const ui = SwaggerUIBundle({
            url: "/api/swagger.json",
            dom_id: '#swagger-ui'
        })
        </script>
        """
        result = _detect_swagger_spec_url(html, "https://example.com/docs")
        assert result == "https://example.com/api/swagger.json"

    def test_spec_url_attribute(self):
        html = '<redoc spec-url="/openapi.yaml"></redoc>'
        result = _detect_swagger_spec_url(html, "https://example.com/")
        assert result == "https://example.com/openapi.yaml"

    def test_redoc_init(self):
        html = """
        <script>
        Redoc.init("https://cdn.example.com/spec.json")
        </script>
        """
        result = _detect_swagger_spec_url(html, "https://example.com/")
        assert result == "https://cdn.example.com/spec.json"

    def test_plain_html_returns_none(self):
        html = "<html><body><h1>Hello</h1></body></html>"
        result = _detect_swagger_spec_url(html, "https://example.com/")
        assert result is None

    def test_relative_url_resolved(self):
        html = """<script>SwaggerUIBundle({url: "spec.yaml"})</script>"""
        result = _detect_swagger_spec_url(html, "https://example.com/docs/index.html")
        assert result == "https://example.com/docs/spec.yaml"


# ---------------------------------------------------------------------------
# _fetch_url (with mock)
# ---------------------------------------------------------------------------

class TestFetchUrl:
    def test_returns_tuple(self):
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.url = "https://example.com/api.json"
        mock_resp.read.return_value = b'{"openapi": "3.0.0"}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            body, ct, url = _fetch_url("https://example.com/api.json")

        assert body == b'{"openapi": "3.0.0"}'
        assert ct == "application/json"
        assert url == "https://example.com/api.json"


# ---------------------------------------------------------------------------
# _url_hash
# ---------------------------------------------------------------------------

class TestUrlHash:
    def test_deterministic(self):
        url = "https://example.com/api"
        expected = hashlib.sha256(url.encode("utf-8")).hexdigest()
        assert _url_hash(url) == expected

    def test_different_urls(self):
        assert _url_hash("http://a.com") != _url_hash("http://b.com")


# ---------------------------------------------------------------------------
# _url_to_filename
# ---------------------------------------------------------------------------

class TestUrlToFilename:
    def test_with_title(self):
        result = _url_to_filename("https://example.com/docs", title="My API Docs")
        assert result == "My API Docs.md"

    def test_without_title(self):
        result = _url_to_filename("https://example.com/api/v1/docs")
        assert result.endswith(".md")
        assert "example.com" in result

    def test_long_url_truncated(self):
        long_url = "https://example.com/" + "a" * 200
        result = _url_to_filename(long_url)
        assert len(result) <= 124  # 120 + ".md"

    def test_special_chars_replaced(self):
        result = _url_to_filename("https://example.com/docs?q=test&v=1", title="Test: API <v2>")
        assert ":" not in result.replace(".md", "")
        assert "<" not in result
        assert ">" not in result


# ---------------------------------------------------------------------------
# _resolve_web_output_path
# ---------------------------------------------------------------------------

class TestResolveWebOutputPath:
    def test_explicit_output(self):
        result = _resolve_web_output_path("http://x.com", None, "/out.md", None)
        assert result == "/out.md"

    def test_with_output_dir(self, tmp_path):
        result = _resolve_web_output_path("http://x.com", "Title", None, str(tmp_path))
        assert str(tmp_path) in result
        assert EXPORT_SUBFOLDER in result
        assert result.endswith(".md")


# ---------------------------------------------------------------------------
# _detect_wait_for
# ---------------------------------------------------------------------------

class TestDetectWaitFor:
    def test_user_provided(self):
        assert _detect_wait_for("http://x.com", "css:.content") == "css:.content"

    def test_user_empty_string(self):
        assert _detect_wait_for("http://x.com", "") is None

    def test_default(self):
        assert _detect_wait_for("http://x.com", None) is None
