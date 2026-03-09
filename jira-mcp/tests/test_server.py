"""Tests for jira-mcp server: helpers, formatting, read and write tool functions."""

import base64
import os
from unittest.mock import patch, MagicMock, call

import httpx
import pytest

import server


# ---------------------------------------------------------------------------
# Sample API responses
# ---------------------------------------------------------------------------

SAMPLE_ISSUE = {
    "key": "PROJ-42",
    "fields": {
        "summary": "Fix login bug",
        "status": {"name": "Open"},
        "priority": {"name": "High"},
        "issuetype": {"name": "Bug"},
        "assignee": {"displayName": "Alice", "name": "alice"},
        "reporter": {"displayName": "Bob", "name": "bob"},
        "created": "2026-01-10T10:00:00.000+0000",
        "updated": "2026-01-15T14:30:00.000+0000",
        "resolution": None,
        "components": [{"name": "Auth"}, {"name": "UI"}],
        "labels": ["security", "urgent"],
        "fixVersions": [{"name": "2.1.0"}],
        "description": "Users cannot log in with SSO.",
    },
}

SAMPLE_ISSUE_MINIMAL = {
    "key": "PROJ-1",
    "fields": {
        "summary": "Minimal issue",
        "status": {"name": "Done"},
        "priority": None,
        "issuetype": {"name": "Task"},
        "assignee": None,
        "reporter": None,
        "created": "2026-01-01T00:00:00.000+0000",
        "updated": "2026-01-01T00:00:00.000+0000",
        "resolution": {"name": "Fixed"},
        "components": [],
        "labels": [],
        "fixVersions": [],
        "description": None,
    },
}

SAMPLE_SEARCH_RESPONSE = {
    "total": 2,
    "issues": [
        {
            "key": "PROJ-10",
            "fields": {
                "summary": "First issue",
                "status": {"name": "Open"},
                "priority": {"name": "Medium"},
                "assignee": {"displayName": "Carol"},
            },
        },
        {
            "key": "PROJ-11",
            "fields": {
                "summary": "Second issue",
                "status": {"name": "Closed"},
                "priority": None,
                "assignee": None,
            },
        },
    ],
}

SAMPLE_COMMENTS_RESPONSE = {
    "comments": [
        {
            "author": {"displayName": "Alice", "name": "alice"},
            "created": "2026-01-12T09:00:00.000+0000",
            "body": "I can reproduce this.",
        },
        {
            "author": {"displayName": "Bob", "name": "bob"},
            "created": "2026-01-12T10:00:00.000+0000",
            "body": "Fixed in commit abc123.",
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


# ---------------------------------------------------------------------------
# _auth_header
# ---------------------------------------------------------------------------

class TestAuthHeader:
    def test_basic_encoding(self):
        with patch.object(server, "JIRA_USERNAME", "user"), \
             patch.object(server, "JIRA_PASSWORD", "pass"):
            header = server._auth_header()
            expected = base64.b64encode(b"user:pass").decode()
            assert header == f"Basic {expected}"

    def test_empty_credentials(self):
        with patch.object(server, "JIRA_USERNAME", ""), \
             patch.object(server, "JIRA_PASSWORD", ""):
            header = server._auth_header()
            expected = base64.b64encode(b":").decode()
            assert header == f"Basic {expected}"

    def test_special_characters(self):
        with patch.object(server, "JIRA_USERNAME", "user@domain"), \
             patch.object(server, "JIRA_PASSWORD", "p@ss:w0rd!"):
            header = server._auth_header()
            expected = base64.b64encode(b"user@domain:p@ss:w0rd!").decode()
            assert header == f"Basic {expected}"


# ---------------------------------------------------------------------------
# _format_user
# ---------------------------------------------------------------------------

class TestFormatUser:
    def test_full_user(self):
        assert server._format_user({"displayName": "Alice", "name": "alice"}) == "Alice (alice)"

    def test_none_user(self):
        assert server._format_user(None) == "N/A"

    def test_empty_dict(self):
        assert server._format_user({}) == "N/A"

    def test_missing_name(self):
        assert server._format_user({"displayName": "Alice"}) == "Alice ()"

    def test_missing_display_name(self):
        assert server._format_user({"name": "alice"}) == "N/A (alice)"


# ---------------------------------------------------------------------------
# _format_issue
# ---------------------------------------------------------------------------

class TestFormatIssue:
    def test_full_issue(self):
        with patch.object(server, "JIRA_URL", "https://jira.example.com"):
            result = server._format_issue(SAMPLE_ISSUE)

        assert "# [PROJ-42] Fix login bug" in result
        assert "**Status:** Open" in result
        assert "**Priority:** High" in result
        assert "**Resolution:** Unresolved" in result
        assert "**Assignee:** Alice (alice)" in result
        assert "**Reporter:** Bob (bob)" in result
        assert "**Components:** Auth, UI" in result
        assert "**Labels:** security, urgent" in result
        assert "**Fix Versions:** 2.1.0" in result
        assert "Users cannot log in with SSO." in result
        assert "https://jira.example.com/browse/PROJ-42" in result

    def test_minimal_issue(self):
        with patch.object(server, "JIRA_URL", "https://jira.example.com"):
            result = server._format_issue(SAMPLE_ISSUE_MINIMAL)

        assert "# [PROJ-1] Minimal issue" in result
        assert "**Priority:** N/A" in result
        assert "**Resolution:** Fixed" in result
        assert "**Assignee:** N/A" in result
        assert "**Reporter:** N/A" in result
        assert "**Components:** N/A" in result
        assert "**Labels:** N/A" in result
        assert "**Fix Versions:** N/A" in result
        assert "No description" in result

    def test_empty_fields(self):
        with patch.object(server, "JIRA_URL", ""):
            result = server._format_issue({"key": "X-1", "fields": {}})

        assert "[X-1]" in result
        assert "N/A" in result


# ---------------------------------------------------------------------------
# _format_comment
# ---------------------------------------------------------------------------

class TestFormatComment:
    def test_full_comment(self):
        comment = {
            "author": {"displayName": "Alice", "name": "alice"},
            "created": "2026-01-12T09:00:00.000+0000",
            "body": "Looks good.",
        }
        result = server._format_comment(comment)
        assert "Alice (alice)" in result
        assert "2026-01-12" in result
        assert "Looks good." in result

    def test_missing_author(self):
        result = server._format_comment({"body": "text"})
        assert "N/A" in result
        assert "text" in result

    def test_empty_body(self):
        result = server._format_comment({"author": {"displayName": "X", "name": "x"}, "body": ""})
        assert "X (x)" in result


# ---------------------------------------------------------------------------
# get_issue
# ---------------------------------------------------------------------------

class TestGetIssue:
    def test_success(self):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = _mock_response(SAMPLE_ISSUE)

        with patch.object(server, "_client", return_value=mock_client), \
             patch.object(server, "JIRA_URL", "https://jira.example.com"):
            result = server.get_issue("PROJ-42")

        mock_client.get.assert_called_once_with("/issue/PROJ-42")
        assert "PROJ-42" in result
        assert "Fix login bug" in result

    def test_http_error(self):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = _mock_response({}, status_code=404)

        with patch.object(server, "_client", return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError):
                server.get_issue("NONEXISTENT-999")


# ---------------------------------------------------------------------------
# search_issues
# ---------------------------------------------------------------------------

class TestSearchIssues:
    def test_results_found(self):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = _mock_response(SAMPLE_SEARCH_RESPONSE)

        with patch.object(server, "_client", return_value=mock_client):
            result = server.search_issues("project = PROJ")

        assert "Found 2 issue(s)" in result
        assert "PROJ-10" in result
        assert "PROJ-11" in result
        assert "First issue" in result
        assert "Second issue" in result

    def test_no_results(self):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = _mock_response({"total": 0, "issues": []})

        with patch.object(server, "_client", return_value=mock_client):
            result = server.search_issues("project = EMPTY")

        assert "No issues found" in result

    def test_max_results_capped_at_50(self):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = _mock_response({"total": 0, "issues": []})

        with patch.object(server, "_client", return_value=mock_client):
            server.search_issues("project = X", max_results=999)

        call_args = mock_client.get.call_args
        assert call_args[1]["params"]["maxResults"] == 50

    def test_unassigned_issue(self):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = _mock_response(SAMPLE_SEARCH_RESPONSE)

        with patch.object(server, "_client", return_value=mock_client):
            result = server.search_issues("project = PROJ")

        assert "Unassigned" in result

    def test_http_error(self):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = _mock_response({}, status_code=401)

        with patch.object(server, "_client", return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError):
                server.search_issues("project = X")


# ---------------------------------------------------------------------------
# get_issue_comments
# ---------------------------------------------------------------------------

class TestGetIssueComments:
    def test_with_comments(self):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = _mock_response(SAMPLE_COMMENTS_RESPONSE)

        with patch.object(server, "_client", return_value=mock_client):
            result = server.get_issue_comments("PROJ-42")

        mock_client.get.assert_called_once_with("/issue/PROJ-42/comment")
        assert "Comments for PROJ-42 (2)" in result
        assert "Alice (alice)" in result
        assert "I can reproduce this." in result
        assert "Bob (bob)" in result
        assert "Fixed in commit abc123." in result

    def test_no_comments(self):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = _mock_response({"comments": []})

        with patch.object(server, "_client", return_value=mock_client):
            result = server.get_issue_comments("PROJ-1")

        assert "No comments found for PROJ-1" in result

    def test_http_error(self):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = _mock_response({}, status_code=500)

        with patch.object(server, "_client", return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError):
                server.get_issue_comments("PROJ-1")


# ===========================================================================
# Write operations
# ===========================================================================

SAMPLE_TRANSITIONS = {
    "transitions": [
        {"id": "21", "name": "In Progress", "to": {"name": "In Progress"}},
        {"id": "31", "name": "Done", "to": {"name": "Done"}},
    ]
}


def _make_mock_client(*responses):
    """Create a mock client. If multiple responses given, side_effect is used."""
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    if len(responses) == 1:
        mock_client.get.return_value = responses[0]
        mock_client.post.return_value = responses[0]
        mock_client.put.return_value = responses[0]
    return mock_client


# ---------------------------------------------------------------------------
# _parse_jira_error
# ---------------------------------------------------------------------------

class TestParseJiraError:
    def test_field_errors(self):
        resp = _mock_response(
            {"errorMessages": [], "errors": {"field1": "bad value"}}, status_code=400
        )
        assert "field1: bad value" in server._parse_jira_error(resp)

    def test_error_messages(self):
        resp = _mock_response(
            {"errorMessages": ["Something broke"], "errors": {}}, status_code=500
        )
        assert "Something broke" in server._parse_jira_error(resp)

    def test_combined(self):
        resp = _mock_response(
            {"errorMessages": ["Msg"], "errors": {"f": "E"}}, status_code=400
        )
        result = server._parse_jira_error(resp)
        assert "Msg" in result
        assert "f: E" in result

    def test_non_json_response(self):
        resp = MagicMock(spec=httpx.Response)
        resp.json.side_effect = ValueError("not json")
        resp.text = "raw error text"
        assert server._parse_jira_error(resp) == "raw error text"


# ---------------------------------------------------------------------------
# _get_transitions
# ---------------------------------------------------------------------------

class TestGetTransitions:
    def test_returns_transitions(self):
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_response(SAMPLE_TRANSITIONS)

        result = server._get_transitions(mock_client, "PROJ-1")

        mock_client.get.assert_called_once_with("/issue/PROJ-1/transitions")
        assert len(result) == 2
        assert result[0]["name"] == "In Progress"
        assert result[1]["name"] == "Done"

    def test_empty_transitions(self):
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_response({"transitions": []})

        result = server._get_transitions(mock_client, "PROJ-1")
        assert result == []

    def test_http_error(self):
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_response({}, status_code=404)

        with pytest.raises(httpx.HTTPStatusError):
            server._get_transitions(mock_client, "NONEXISTENT-1")


# ---------------------------------------------------------------------------
# _load_default_custom_fields
# ---------------------------------------------------------------------------

class TestLoadDefaultCustomFields:
    def test_valid_json(self):
        with patch.dict(os.environ, {"JIRA_DEFAULT_CUSTOM_FIELDS": '{"cf_1": {"value": "X"}}'}):
            result = server._load_default_custom_fields()
        assert result == {"cf_1": {"value": "X"}}

    def test_empty_string(self):
        with patch.dict(os.environ, {"JIRA_DEFAULT_CUSTOM_FIELDS": ""}):
            result = server._load_default_custom_fields()
        assert result == {}

    def test_missing_env(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JIRA_DEFAULT_CUSTOM_FIELDS", None)
            result = server._load_default_custom_fields()
        assert result == {}

    def test_invalid_json(self):
        with patch.dict(os.environ, {"JIRA_DEFAULT_CUSTOM_FIELDS": "not json"}):
            result = server._load_default_custom_fields()
        assert result == {}


# ---------------------------------------------------------------------------
# create_issue
# ---------------------------------------------------------------------------

class TestCreateIssue:
    def test_preview_minimal(self):
        with patch.object(server, "JIRA_USERNAME", ""), \
             patch.object(server, "JIRA_DEFAULT_CUSTOM_FIELDS", {}):
            result = server.create_issue("New task", project_key="PROJ", execute=False)

        assert "Preview: Create Issue" in result
        assert "**Project:** PROJ" in result
        assert "**Summary:** New task" in result
        assert "**Type:** Task" in result
        assert "**Assignee:** (unassigned)" in result
        assert "preview" in result.lower()
        assert "execute=True" in result

    def test_preview_uses_default_project(self):
        with patch.object(server, "JIRA_DEFAULT_PROJECT", "DEF"), \
             patch.object(server, "JIRA_USERNAME", ""), \
             patch.object(server, "JIRA_DEFAULT_CUSTOM_FIELDS", {}):
            result = server.create_issue("Task title", execute=False)

        assert "**Project:** DEF" in result

    def test_preview_no_project_returns_error(self):
        with patch.object(server, "JIRA_DEFAULT_PROJECT", ""), \
             patch.object(server, "JIRA_USERNAME", ""):
            result = server.create_issue("Task title", execute=False)

        assert "Error" in result
        assert "project_key" in result

    def test_preview_default_assignee_from_username(self):
        with patch.object(server, "JIRA_USERNAME", "current.user"), \
             patch.object(server, "JIRA_DEFAULT_CUSTOM_FIELDS", {}):
            result = server.create_issue("Task", project_key="PROJ", execute=False)

        assert "**Assignee:** current.user" in result

    def test_preview_default_issue_type(self):
        with patch.object(server, "JIRA_DEFAULT_ISSUE_TYPE", "Bug"), \
             patch.object(server, "JIRA_USERNAME", ""), \
             patch.object(server, "JIRA_DEFAULT_CUSTOM_FIELDS", {}):
            result = server.create_issue("Title", project_key="PROJ", execute=False)

        assert "**Type:** Bug" in result

    def test_preview_explicit_issue_type_overrides_default(self):
        with patch.object(server, "JIRA_DEFAULT_ISSUE_TYPE", "Bug"), \
             patch.object(server, "JIRA_USERNAME", ""), \
             patch.object(server, "JIRA_DEFAULT_CUSTOM_FIELDS", {}):
            result = server.create_issue("Title", project_key="PROJ", issue_type="Story", execute=False)

        assert "**Type:** Story" in result

    def test_preview_default_custom_fields(self):
        defaults = {"customfield_11010": {"value": "Stable"}}
        with patch.object(server, "JIRA_DEFAULT_CUSTOM_FIELDS", defaults), \
             patch.object(server, "JIRA_USERNAME", ""):
            result = server.create_issue("Title", project_key="PROJ", execute=False)

        assert "customfield_11010" in result

    def test_preview_explicit_custom_fields_override_defaults(self):
        defaults = {"customfield_11010": {"value": "Stable"}, "customfield_99": "default"}
        overrides = {"customfield_11010": {"value": "Always"}}
        with patch.object(server, "JIRA_DEFAULT_CUSTOM_FIELDS", defaults), \
             patch.object(server, "JIRA_USERNAME", ""):
            result = server.create_issue(
                "Title", project_key="PROJ", custom_fields=overrides, execute=False,
            )

        assert "customfield_11010" in result
        assert "customfield_99" in result

    def test_preview_full(self):
        result = server.create_issue(
            "Bug title", project_key="PROJ", issue_type="Bug", description="Details",
            priority="High", assignee="alice", labels=["ui", "urgent"],
            components=["Auth", "API"], execute=False,
        )

        assert "**Type:** Bug" in result
        assert "**Description:** Details" in result
        assert "**Priority:** High" in result
        assert "**Assignee:** alice" in result
        assert "ui, urgent" in result
        assert "Auth, API" in result

    def test_preview_does_not_call_api(self):
        with patch.object(server, "_client") as mock_client_fn:
            server.create_issue("Test", project_key="PROJ", execute=False)

        mock_client_fn.assert_not_called()

    def test_preview_with_custom_fields(self):
        result = server.create_issue(
            "Task", project_key="PROJ", custom_fields={"customfield_11010": {"value": "Stable"}},
            execute=False,
        )

        assert "**Custom fields:** customfield_11010" in result

    def test_execute_success(self):
        mock_client = _make_mock_client(_mock_response({"key": "PROJ-99"}))

        with patch.object(server, "_client", return_value=mock_client), \
             patch.object(server, "JIRA_URL", "https://jira.example.com"), \
             patch.object(server, "JIRA_DEFAULT_CUSTOM_FIELDS", {}):
            result = server.create_issue("New task", project_key="PROJ", execute=True)

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "/issue"
        payload = call_args[1]["json"]["fields"]
        assert payload["project"]["key"] == "PROJ"
        assert payload["summary"] == "New task"
        assert payload["issuetype"]["name"] == "Task"
        assert "assignee" not in payload

        assert "PROJ-99" in result
        assert "created successfully" in result
        assert "https://jira.example.com/browse/PROJ-99" in result

    def test_execute_with_optional_fields(self):
        mock_client = _make_mock_client(_mock_response({"key": "PROJ-100"}))
        mock_client.put.return_value = _mock_response({}, status_code=204)

        with patch.object(server, "_client", return_value=mock_client), \
             patch.object(server, "JIRA_URL", "https://jira.example.com"), \
             patch.object(server, "JIRA_DEFAULT_CUSTOM_FIELDS", {}):
            result = server.create_issue(
                "Full task", project_key="PROJ", issue_type="Bug",
                description="Desc", priority="High", assignee="bob",
                labels=["x"], components=["UI"], execute=True,
            )

        payload = mock_client.post.call_args[1]["json"]["fields"]
        assert payload["description"] == "Desc"
        assert payload["priority"] == {"name": "High"}
        assert "assignee" not in payload
        assert payload["labels"] == ["x"]
        assert payload["components"] == [{"name": "UI"}]

        assert "Assigned to:** bob" in result

    def test_execute_assignee_not_in_create_payload(self):
        mock_client = _make_mock_client(_mock_response({"key": "PROJ-50"}))
        mock_client.put.return_value = _mock_response({}, status_code=204)

        with patch.object(server, "_client", return_value=mock_client), \
             patch.object(server, "JIRA_URL", ""), \
             patch.object(server, "JIRA_DEFAULT_CUSTOM_FIELDS", {}):
            server.create_issue("T", project_key="PROJ", assignee="alice", execute=True)

        create_payload = mock_client.post.call_args[1]["json"]["fields"]
        assert "assignee" not in create_payload

        mock_client.put.assert_called_once_with(
            "/issue/PROJ-50/assignee", json={"name": "alice"}
        )

    def test_execute_auto_assigns_after_create(self):
        mock_client = _make_mock_client(_mock_response({"key": "PROJ-55"}))
        mock_client.put.return_value = _mock_response({}, status_code=204)

        with patch.object(server, "_client", return_value=mock_client), \
             patch.object(server, "JIRA_URL", "https://jira.example.com"), \
             patch.object(server, "JIRA_DEFAULT_CUSTOM_FIELDS", {}):
            result = server.create_issue("Task", project_key="PROJ", assignee="carol", execute=True)

        assert "PROJ-55" in result
        assert "created successfully" in result
        assert "Assigned to:** carol" in result
        mock_client.put.assert_called_once()

    def test_execute_assign_failure_still_returns_created(self):
        mock_client = _make_mock_client(_mock_response({"key": "PROJ-60"}))
        assign_err = _mock_response(
            {"errorMessages": [], "errors": {"assignee": "User does not exist"}},
            status_code=400,
        )
        assign_err.raise_for_status = MagicMock()
        mock_client.put.return_value = assign_err

        with patch.object(server, "_client", return_value=mock_client), \
             patch.object(server, "JIRA_URL", "https://jira.example.com"), \
             patch.object(server, "JIRA_DEFAULT_CUSTOM_FIELDS", {}):
            result = server.create_issue("Task", project_key="PROJ", assignee="nobody", execute=True)

        assert "PROJ-60" in result
        assert "created successfully" in result
        assert "Warning" in result
        assert "nobody" in result

    def test_execute_with_custom_fields(self):
        mock_client = _make_mock_client(_mock_response({"key": "PROJ-70"}))

        with patch.object(server, "_client", return_value=mock_client), \
             patch.object(server, "JIRA_URL", ""), \
             patch.object(server, "JIRA_DEFAULT_CUSTOM_FIELDS", {}):
            server.create_issue(
                "Bug", project_key="PROJ", custom_fields={"customfield_11010": {"value": "Stable"}},
                execute=True,
            )

        payload = mock_client.post.call_args[1]["json"]["fields"]
        assert payload["customfield_11010"] == {"value": "Stable"}

    def test_execute_default_custom_fields_applied(self):
        defaults = {"customfield_11010": {"value": "Stable"}}
        mock_client = _make_mock_client(_mock_response({"key": "PROJ-71"}))

        with patch.object(server, "_client", return_value=mock_client), \
             patch.object(server, "JIRA_URL", ""), \
             patch.object(server, "JIRA_DEFAULT_CUSTOM_FIELDS", defaults):
            server.create_issue("Task", project_key="PROJ", execute=True)

        payload = mock_client.post.call_args[1]["json"]["fields"]
        assert payload["customfield_11010"] == {"value": "Stable"}

    def test_execute_explicit_custom_fields_override_defaults(self):
        defaults = {"customfield_11010": {"value": "Stable"}, "customfield_99": "keep"}
        overrides = {"customfield_11010": {"value": "Always"}}
        mock_client = _make_mock_client(_mock_response({"key": "PROJ-72"}))

        with patch.object(server, "_client", return_value=mock_client), \
             patch.object(server, "JIRA_URL", ""), \
             patch.object(server, "JIRA_DEFAULT_CUSTOM_FIELDS", defaults):
            server.create_issue(
                "Task", project_key="PROJ", custom_fields=overrides, execute=True,
            )

        payload = mock_client.post.call_args[1]["json"]["fields"]
        assert payload["customfield_11010"] == {"value": "Always"}
        assert payload["customfield_99"] == "keep"

    def test_execute_default_issue_type_applied(self):
        mock_client = _make_mock_client(_mock_response({"key": "PROJ-73"}))

        with patch.object(server, "_client", return_value=mock_client), \
             patch.object(server, "JIRA_URL", ""), \
             patch.object(server, "JIRA_DEFAULT_ISSUE_TYPE", "Bug"), \
             patch.object(server, "JIRA_DEFAULT_CUSTOM_FIELDS", {}):
            server.create_issue("Task", project_key="PROJ", execute=True)

        payload = mock_client.post.call_args[1]["json"]["fields"]
        assert payload["issuetype"]["name"] == "Bug"

    def test_execute_jira_error_returns_details(self):
        error_body = {
            "errorMessages": [],
            "errors": {"customfield_11010": "Reproducibility is required."},
        }
        mock_client = _make_mock_client(_mock_response(error_body, status_code=400))

        with patch.object(server, "_client", return_value=mock_client):
            result = server.create_issue("Bad", project_key="PROJ", execute=True)

        assert "Error creating issue" in result
        assert "400" in result
        assert "Reproducibility is required" in result

    def test_execute_jira_error_multiple_messages(self):
        error_body = {
            "errorMessages": ["Something went wrong"],
            "errors": {"summary": "Summary is required"},
        }
        mock_client = _make_mock_client(_mock_response(error_body, status_code=400))

        with patch.object(server, "_client", return_value=mock_client):
            result = server.create_issue("", project_key="PROJ", execute=True)

        assert "Something went wrong" in result
        assert "Summary is required" in result


# ---------------------------------------------------------------------------
# add_comment
# ---------------------------------------------------------------------------

class TestAddComment:
    def test_preview(self):
        result = server.add_comment("PROJ-1", "Nice work!", execute=False)

        assert "Preview: Add Comment" in result
        assert "**Issue:** PROJ-1" in result
        assert "Nice work!" in result
        assert "execute=True" in result

    def test_preview_does_not_call_api(self):
        with patch.object(server, "_client") as mock_client_fn:
            server.add_comment("PROJ-1", "Test", execute=False)

        mock_client_fn.assert_not_called()

    def test_execute_success(self):
        mock_client = _make_mock_client(_mock_response({}))

        with patch.object(server, "_client", return_value=mock_client):
            result = server.add_comment("PROJ-1", "Great fix!", execute=True)

        mock_client.post.assert_called_once_with(
            "/issue/PROJ-1/comment", json={"body": "Great fix!"}
        )
        assert "added to **PROJ-1** successfully" in result

    def test_execute_http_error(self):
        mock_client = _make_mock_client(_mock_response({}, status_code=404))

        with patch.object(server, "_client", return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError):
                server.add_comment("BAD-1", "text", execute=True)


# ---------------------------------------------------------------------------
# transition_issue
# ---------------------------------------------------------------------------

class TestTransitionIssue:
    def test_preview_valid_transition(self):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = _mock_response(SAMPLE_TRANSITIONS)

        with patch.object(server, "_client", return_value=mock_client):
            result = server.transition_issue("PROJ-1", "Done", execute=False)

        assert "Preview: Transition Issue" in result
        assert "**Requested transition:** Done" in result
        assert "**Target status:** Done" in result
        assert "execute=True" in result
        mock_client.post.assert_not_called()

    def test_preview_case_insensitive(self):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = _mock_response(SAMPLE_TRANSITIONS)

        with patch.object(server, "_client", return_value=mock_client):
            result = server.transition_issue("PROJ-1", "in progress", execute=False)

        assert "**Target status:** In Progress" in result

    def test_preview_invalid_transition(self):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = _mock_response(SAMPLE_TRANSITIONS)

        with patch.object(server, "_client", return_value=mock_client):
            result = server.transition_issue("PROJ-1", "Reopen", execute=False)

        assert "not found" in result
        assert "In Progress" in result
        assert "Done" in result

    def test_preview_no_transitions_available(self):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = _mock_response({"transitions": []})

        with patch.object(server, "_client", return_value=mock_client):
            result = server.transition_issue("PROJ-1", "Done", execute=False)

        assert "(none)" in result
        assert "not found" in result

    def test_execute_success(self):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = _mock_response(SAMPLE_TRANSITIONS)
        mock_client.post.return_value = _mock_response({}, status_code=204)

        with patch.object(server, "_client", return_value=mock_client):
            result = server.transition_issue("PROJ-1", "Done", execute=True)

        mock_client.post.assert_called_once_with(
            "/issue/PROJ-1/transitions",
            json={"transition": {"id": "31"}},
        )
        assert "transitioned to **Done** successfully" in result

    def test_execute_invalid_transition_does_not_post(self):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = _mock_response(SAMPLE_TRANSITIONS)

        with patch.object(server, "_client", return_value=mock_client):
            result = server.transition_issue("PROJ-1", "Nonexistent", execute=True)

        mock_client.post.assert_not_called()
        assert "not found" in result

    def test_execute_http_error_on_get_transitions(self):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = _mock_response({}, status_code=404)

        with patch.object(server, "_client", return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError):
                server.transition_issue("BAD-1", "Done", execute=True)


# ---------------------------------------------------------------------------
# assign_issue
# ---------------------------------------------------------------------------

class TestAssignIssue:
    def test_preview(self):
        result = server.assign_issue("PROJ-1", "alice", execute=False)

        assert "Preview: Assign Issue" in result
        assert "**Issue:** PROJ-1" in result
        assert "**New assignee:** alice" in result
        assert "execute=True" in result

    def test_preview_does_not_call_api(self):
        with patch.object(server, "_client") as mock_client_fn:
            server.assign_issue("PROJ-1", "alice", execute=False)

        mock_client_fn.assert_not_called()

    def test_execute_success(self):
        mock_client = _make_mock_client(_mock_response({}, status_code=204))

        with patch.object(server, "_client", return_value=mock_client):
            result = server.assign_issue("PROJ-1", "alice", execute=True)

        mock_client.put.assert_called_once_with(
            "/issue/PROJ-1/assignee", json={"name": "alice"}
        )
        assert "assigned to **alice** successfully" in result

    def test_execute_http_error(self):
        mock_client = _make_mock_client(_mock_response({}, status_code=401))

        with patch.object(server, "_client", return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError):
                server.assign_issue("PROJ-1", "nobody", execute=True)


# ---------------------------------------------------------------------------
# link_issues
# ---------------------------------------------------------------------------

class TestLinkIssues:
    def test_preview(self):
        result = server.link_issues("PROJ-1", "PROJ-2", "Blocks", execute=False)

        assert "Preview: Link Issues" in result
        assert "**Link type:** Blocks" in result
        assert "**Inward issue:** PROJ-1" in result
        assert "**Outward issue:** PROJ-2" in result
        assert "execute=True" in result

    def test_preview_default_link_type(self):
        result = server.link_issues("PROJ-1", "PROJ-2", execute=False)

        assert "**Link type:** Relates" in result

    def test_preview_does_not_call_api(self):
        with patch.object(server, "_client") as mock_client_fn:
            server.link_issues("PROJ-1", "PROJ-2", execute=False)

        mock_client_fn.assert_not_called()

    def test_execute_success(self):
        mock_client = _make_mock_client(_mock_response({}, status_code=201))

        with patch.object(server, "_client", return_value=mock_client):
            result = server.link_issues("PROJ-1", "PROJ-2", "Blocks", execute=True)

        mock_client.post.assert_called_once_with("/issueLink", json={
            "type": {"name": "Blocks"},
            "inwardIssue": {"key": "PROJ-1"},
            "outwardIssue": {"key": "PROJ-2"},
        })
        assert "Link **Blocks** created" in result
        assert "PROJ-1 -> PROJ-2" in result

    def test_execute_http_error(self):
        mock_client = _make_mock_client(_mock_response({}, status_code=404))

        with patch.object(server, "_client", return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError):
                server.link_issues("BAD-1", "BAD-2", "Blocks", execute=True)
