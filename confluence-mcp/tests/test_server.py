"""Tests for confluence-mcp server: helpers, formatting, and tool functions."""

import base64
from unittest.mock import patch, MagicMock

import httpx
import pytest

import server


# ---------------------------------------------------------------------------
# Sample API responses
# ---------------------------------------------------------------------------

SAMPLE_PAGE = {
    "id": "12345",
    "title": "Architecture Overview",
    "space": {"key": "DEV"},
    "version": {"number": 3, "by": {"displayName": "Alice"}, "when": "2026-01-15T10:00:00.000Z"},
    "_links": {"webui": "/display/DEV/Architecture+Overview"},
    "body": {
        "storage": {
            "value": "<h1>Overview</h1><p>This is the &amp; architecture guide.</p><p>Second paragraph.</p>"
        }
    },
}

SAMPLE_PAGE_EMPTY_BODY = {
    "id": "99999",
    "title": "Empty Page",
    "space": {"key": "TEST"},
    "version": {"number": 1, "by": {"displayName": "Bob"}, "when": "2026-01-01T00:00:00.000Z"},
    "_links": {"webui": "/display/TEST/Empty+Page"},
    "body": {"storage": {"value": ""}},
}

SAMPLE_SEARCH_RESPONSE = {
    "totalSize": 2,
    "size": 2,
    "results": [
        {
            "id": "100",
            "title": "Getting Started",
            "space": {"key": "DEV"},
            "version": {"when": "2026-01-10T08:00:00.000Z"},
            "_links": {"webui": "/display/DEV/Getting+Started"},
        },
        {
            "id": "101",
            "title": "API Reference",
            "space": {"key": "DEV"},
            "version": {"when": "2026-01-12T12:00:00.000Z"},
            "_links": {"webui": "/display/DEV/API+Reference"},
        },
    ],
}

SAMPLE_CHILDREN_RESPONSE = {
    "results": [
        {"id": "200", "title": "Child A", "version": {"when": "2026-01-05T00:00:00.000Z"}},
        {"id": "201", "title": "Child B", "version": {"when": "2026-01-06T00:00:00.000Z"}},
    ]
}

SAMPLE_SPACES_RESPONSE = {
    "results": [
        {"key": "DEV", "name": "Development", "type": "global"},
        {"key": "HR", "name": "Human Resources", "type": "global"},
        {"key": "~alice", "name": "Alice Personal", "type": "personal"},
    ]
}

SAMPLE_COMMENTS_RESPONSE = {
    "results": [
        {
            "version": {"by": {"displayName": "Carol"}, "when": "2026-01-20T09:00:00.000Z"},
            "body": {"storage": {"value": "<p>Great document!</p>"}},
        },
        {
            "version": {"by": {"displayName": "Dave"}, "when": "2026-01-21T11:00:00.000Z"},
            "body": {"storage": {"value": ""}},
        },
    ]
}


def _mock_response(json_data, status_code=200):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    return resp


def _make_mock_client(response):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = response
    return mock_client


# ---------------------------------------------------------------------------
# _auth_header
# ---------------------------------------------------------------------------

class TestAuthHeader:
    def test_basic_encoding(self):
        with patch.object(server, "CONFLUENCE_USERNAME", "admin"), \
             patch.object(server, "CONFLUENCE_PASSWORD", "secret"):
            header = server._auth_header()
            expected = base64.b64encode(b"admin:secret").decode()
            assert header == f"Basic {expected}"

    def test_empty_credentials(self):
        with patch.object(server, "CONFLUENCE_USERNAME", ""), \
             patch.object(server, "CONFLUENCE_PASSWORD", ""):
            header = server._auth_header()
            expected = base64.b64encode(b":").decode()
            assert header == f"Basic {expected}"

    def test_special_characters(self):
        with patch.object(server, "CONFLUENCE_USERNAME", "user@corp"), \
             patch.object(server, "CONFLUENCE_PASSWORD", "p@ss:w0rd!"):
            header = server._auth_header()
            expected = base64.b64encode(b"user@corp:p@ss:w0rd!").decode()
            assert header == f"Basic {expected}"


# ---------------------------------------------------------------------------
# _html_to_text
# ---------------------------------------------------------------------------

class TestHtmlToText:
    def test_strips_tags(self):
        assert server._html_to_text("<b>bold</b>") == "bold"

    def test_br_to_newline(self):
        result = server._html_to_text("line1<br/>line2")
        assert "line1" in result
        assert "line2" in result

    def test_block_elements_to_newline(self):
        result = server._html_to_text("<p>para1</p><p>para2</p>")
        lines = [l for l in result.splitlines() if l.strip()]
        assert len(lines) == 2

    def test_decodes_entities(self):
        result = server._html_to_text("&amp; &lt; &gt; &quot; &nbsp; &#39;")
        assert "&" in result
        assert "<" in result
        assert ">" in result
        assert '"' in result
        assert "'" in result

    def test_empty_string(self):
        assert server._html_to_text("") == ""

    def test_nested_tags(self):
        result = server._html_to_text("<div><p><b>hello</b> world</p></div>")
        assert "hello" in result
        assert "world" in result


# ---------------------------------------------------------------------------
# _format_page
# ---------------------------------------------------------------------------

class TestFormatPage:
    def test_full_page(self):
        with patch.object(server, "CONFLUENCE_URL", "https://wiki.example.com"):
            result = server._format_page(SAMPLE_PAGE)

        assert "# Architecture Overview" in result
        assert "**Space:** DEV" in result
        assert "**Page ID:** 12345" in result
        assert "**Version:** 3" in result
        assert "**Last modified by:** Alice" in result
        assert "https://wiki.example.com/display/DEV/Architecture+Overview" in result
        assert "## Content" in result
        assert "architecture guide" in result

    def test_empty_body(self):
        with patch.object(server, "CONFLUENCE_URL", "https://wiki.example.com"):
            result = server._format_page(SAMPLE_PAGE_EMPTY_BODY)

        assert "(empty page)" in result

    def test_without_body(self):
        with patch.object(server, "CONFLUENCE_URL", "https://wiki.example.com"):
            result = server._format_page(SAMPLE_PAGE, include_body=False)

        assert "## Content" not in result

    def test_missing_fields(self):
        with patch.object(server, "CONFLUENCE_URL", ""):
            result = server._format_page({"id": "1", "title": "T"})

        assert "# T" in result
        assert "N/A" in result

    def test_no_body_key(self):
        page = {"id": "1", "title": "T", "space": {"key": "S"}, "version": {"number": 1, "by": {"displayName": "A"}, "when": "2026-01-01"}, "_links": {"webui": "/x"}}
        with patch.object(server, "CONFLUENCE_URL", ""):
            result = server._format_page(page)

        assert "(empty page)" in result


# ---------------------------------------------------------------------------
# get_page
# ---------------------------------------------------------------------------

class TestGetPage:
    def test_success(self):
        mock_client = _make_mock_client(_mock_response(SAMPLE_PAGE))

        with patch.object(server, "_client", return_value=mock_client), \
             patch.object(server, "CONFLUENCE_URL", "https://wiki.example.com"):
            result = server.get_page("12345")

        mock_client.get.assert_called_once_with(
            "/content/12345",
            params={"expand": "body.storage,version,space"},
        )
        assert "Architecture Overview" in result

    def test_http_error(self):
        mock_client = _make_mock_client(_mock_response({}, status_code=404))

        with patch.object(server, "_client", return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError):
                server.get_page("99999")


# ---------------------------------------------------------------------------
# get_page_by_title
# ---------------------------------------------------------------------------

class TestGetPageByTitle:
    def test_found(self):
        mock_client = _make_mock_client(
            _mock_response({"results": [SAMPLE_PAGE]})
        )

        with patch.object(server, "_client", return_value=mock_client), \
             patch.object(server, "CONFLUENCE_URL", "https://wiki.example.com"):
            result = server.get_page_by_title("DEV", "Architecture Overview")

        call_args = mock_client.get.call_args
        assert call_args[0][0] == "/content"
        assert call_args[1]["params"]["spaceKey"] == "DEV"
        assert call_args[1]["params"]["title"] == "Architecture Overview"
        assert "Architecture Overview" in result

    def test_not_found(self):
        mock_client = _make_mock_client(_mock_response({"results": []}))

        with patch.object(server, "_client", return_value=mock_client):
            result = server.get_page_by_title("DEV", "Nonexistent")

        assert "No page found" in result
        assert "Nonexistent" in result
        assert "DEV" in result

    def test_http_error(self):
        mock_client = _make_mock_client(_mock_response({}, status_code=500))

        with patch.object(server, "_client", return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError):
                server.get_page_by_title("DEV", "Test")


# ---------------------------------------------------------------------------
# search_pages
# ---------------------------------------------------------------------------

class TestSearchPages:
    def test_results_found(self):
        mock_client = _make_mock_client(_mock_response(SAMPLE_SEARCH_RESPONSE))

        with patch.object(server, "_client", return_value=mock_client), \
             patch.object(server, "CONFLUENCE_URL", "https://wiki.example.com"):
            result = server.search_pages('space = DEV AND title ~ "guide"')

        assert "Found 2 page(s)" in result
        assert "Getting Started" in result
        assert "API Reference" in result

    def test_no_results(self):
        mock_client = _make_mock_client(
            _mock_response({"totalSize": 0, "size": 0, "results": []})
        )

        with patch.object(server, "_client", return_value=mock_client):
            result = server.search_pages("title = nothing")

        assert "No pages found" in result

    def test_max_results_capped_at_50(self):
        mock_client = _make_mock_client(
            _mock_response({"totalSize": 0, "size": 0, "results": []})
        )

        with patch.object(server, "_client", return_value=mock_client):
            server.search_pages("space = X", max_results=999)

        call_args = mock_client.get.call_args
        assert call_args[1]["params"]["limit"] == 50

    def test_http_error(self):
        mock_client = _make_mock_client(_mock_response({}, status_code=401))

        with patch.object(server, "_client", return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError):
                server.search_pages("space = X")

    def test_uses_size_fallback(self):
        response = {"size": 1, "results": [{"id": "1", "title": "T", "space": {"key": "X"}, "version": {"when": "2026-01-01"}, "_links": {"webui": "/x"}}]}
        mock_client = _make_mock_client(_mock_response(response))

        with patch.object(server, "_client", return_value=mock_client), \
             patch.object(server, "CONFLUENCE_URL", ""):
            result = server.search_pages("space = X")

        assert "Found 1 page(s)" in result


# ---------------------------------------------------------------------------
# get_page_children
# ---------------------------------------------------------------------------

class TestGetPageChildren:
    def test_with_children(self):
        mock_client = _make_mock_client(_mock_response(SAMPLE_CHILDREN_RESPONSE))

        with patch.object(server, "_client", return_value=mock_client):
            result = server.get_page_children("12345")

        mock_client.get.assert_called_once_with(
            "/content/12345/child/page",
            params={"limit": 25, "expand": "version,space"},
        )
        assert "Child pages of 12345 (2)" in result
        assert "Child A" in result
        assert "Child B" in result

    def test_no_children(self):
        mock_client = _make_mock_client(_mock_response({"results": []}))

        with patch.object(server, "_client", return_value=mock_client):
            result = server.get_page_children("12345")

        assert "No child pages found" in result

    def test_max_results_capped_at_100(self):
        mock_client = _make_mock_client(_mock_response({"results": []}))

        with patch.object(server, "_client", return_value=mock_client):
            server.get_page_children("1", max_results=999)

        call_args = mock_client.get.call_args
        assert call_args[1]["params"]["limit"] == 100

    def test_http_error(self):
        mock_client = _make_mock_client(_mock_response({}, status_code=404))

        with patch.object(server, "_client", return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError):
                server.get_page_children("99999")


# ---------------------------------------------------------------------------
# list_spaces
# ---------------------------------------------------------------------------

class TestListSpaces:
    def test_returns_spaces(self):
        mock_client = _make_mock_client(_mock_response(SAMPLE_SPACES_RESPONSE))

        with patch.object(server, "_client", return_value=mock_client):
            result = server.list_spaces()

        assert "Confluence spaces (3)" in result
        assert "**DEV**" in result
        assert "Development" in result
        assert "**HR**" in result
        assert "Human Resources" in result
        assert "**~alice**" in result
        assert "personal" in result

    def test_no_spaces(self):
        mock_client = _make_mock_client(_mock_response({"results": []}))

        with patch.object(server, "_client", return_value=mock_client):
            result = server.list_spaces()

        assert "No spaces found" in result

    def test_max_results_capped_at_200(self):
        mock_client = _make_mock_client(_mock_response({"results": []}))

        with patch.object(server, "_client", return_value=mock_client):
            server.list_spaces(max_results=999)

        call_args = mock_client.get.call_args
        assert call_args[1]["params"]["limit"] == 200

    def test_http_error(self):
        mock_client = _make_mock_client(_mock_response({}, status_code=500))

        with patch.object(server, "_client", return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError):
                server.list_spaces()


# ---------------------------------------------------------------------------
# get_page_comments
# ---------------------------------------------------------------------------

class TestGetPageComments:
    def test_with_comments(self):
        mock_client = _make_mock_client(_mock_response(SAMPLE_COMMENTS_RESPONSE))

        with patch.object(server, "_client", return_value=mock_client):
            result = server.get_page_comments("12345")

        mock_client.get.assert_called_once_with(
            "/content/12345/child/comment",
            params={"limit": 25, "expand": "body.storage,version"},
        )
        assert "Comments for page 12345 (2)" in result
        assert "Carol" in result
        assert "Great document!" in result
        assert "Dave" in result
        assert "(empty)" in result

    def test_no_comments(self):
        mock_client = _make_mock_client(_mock_response({"results": []}))

        with patch.object(server, "_client", return_value=mock_client):
            result = server.get_page_comments("12345")

        assert "No comments found for page 12345" in result

    def test_max_results_capped_at_100(self):
        mock_client = _make_mock_client(_mock_response({"results": []}))

        with patch.object(server, "_client", return_value=mock_client):
            server.get_page_comments("1", max_results=999)

        call_args = mock_client.get.call_args
        assert call_args[1]["params"]["limit"] == 100

    def test_http_error(self):
        mock_client = _make_mock_client(_mock_response({}, status_code=500))

        with patch.object(server, "_client", return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError):
                server.get_page_comments("12345")
