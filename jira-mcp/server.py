"""MCP server for reading Jira issues (Server/Data Center) via REST API v2."""

import os
import base64
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Jira")

JIRA_URL = os.environ.get("JIRA_URL", "").rstrip("/")
JIRA_USERNAME = os.environ.get("JIRA_USERNAME", "")
JIRA_PASSWORD = os.environ.get("JIRA_PASSWORD", "")


def _auth_header() -> str:
    token = base64.b64encode(f"{JIRA_USERNAME}:{JIRA_PASSWORD}".encode()).decode()
    return f"Basic {token}"


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=f"{JIRA_URL}/rest/api/2",
        headers={
            "Authorization": _auth_header(),
            "Content-Type": "application/json",
        },
        verify=False,
        timeout=30.0,
    )


def _format_user(user: dict | None) -> str:
    if not user:
        return "N/A"
    return f"{user.get('displayName', 'N/A')} ({user.get('name', '')})"


def _format_issue(issue: dict[str, Any]) -> str:
    fields = issue.get("fields", {})
    key = issue.get("key", "???")
    summary = fields.get("summary", "N/A")
    status = fields.get("status", {}).get("name", "N/A")
    priority = fields.get("priority", {}).get("name", "N/A") if fields.get("priority") else "N/A"
    issue_type = fields.get("issuetype", {}).get("name", "N/A")
    assignee = _format_user(fields.get("assignee"))
    reporter = _format_user(fields.get("reporter"))
    created = fields.get("created", "N/A")
    updated = fields.get("updated", "N/A")
    resolution = fields.get("resolution", {}).get("name", "N/A") if fields.get("resolution") else "Unresolved"

    components = ", ".join(c.get("name", "") for c in fields.get("components", [])) or "N/A"
    labels = ", ".join(fields.get("labels", [])) or "N/A"
    fix_versions = ", ".join(v.get("name", "") for v in fields.get("fixVersions", [])) or "N/A"

    description = fields.get("description") or "No description"

    lines = [
        f"# [{key}] {summary}",
        "",
        f"**Type:** {issue_type}",
        f"**Status:** {status}",
        f"**Priority:** {priority}",
        f"**Resolution:** {resolution}",
        f"**Assignee:** {assignee}",
        f"**Reporter:** {reporter}",
        f"**Components:** {components}",
        f"**Labels:** {labels}",
        f"**Fix Versions:** {fix_versions}",
        f"**Created:** {created}",
        f"**Updated:** {updated}",
        f"**URL:** {JIRA_URL}/browse/{key}",
        "",
        "## Description",
        "",
        description,
    ]
    return "\n".join(lines)


def _format_comment(comment: dict[str, Any]) -> str:
    author = _format_user(comment.get("author"))
    created = comment.get("created", "N/A")
    body = comment.get("body", "")
    return f"**{author}** ({created}):\n{body}"


@mcp.tool()
def get_issue(issue_key: str) -> str:
    """Get a Jira issue by key (e.g. ACR-84314). Returns summary, description, status, priority, assignee, reporter, components, labels, and other fields."""
    with _client() as client:
        resp = client.get(f"/issue/{issue_key}")
        resp.raise_for_status()
        issue = resp.json()
    return _format_issue(issue)


@mcp.tool()
def search_issues(jql: str, max_results: int = 20) -> str:
    """Search Jira issues using JQL query. Examples: 'project = ACR AND status = Open', 'assignee = currentUser() ORDER BY updated DESC'. Returns up to max_results issues (default 20)."""
    max_results = min(max_results, 50)
    with _client() as client:
        resp = client.get("/search", params={"jql": jql, "maxResults": max_results})
        resp.raise_for_status()
        data = resp.json()

    issues = data.get("issues", [])
    total = data.get("total", 0)

    if not issues:
        return f"No issues found for JQL: {jql}"

    lines = [f"Found {total} issue(s) (showing {len(issues)}):", ""]
    for issue in issues:
        fields = issue.get("fields", {})
        key = issue.get("key", "???")
        summary = fields.get("summary", "N/A")
        status = fields.get("status", {}).get("name", "N/A")
        assignee_name = fields.get("assignee", {}).get("displayName", "Unassigned") if fields.get("assignee") else "Unassigned"
        priority = fields.get("priority", {}).get("name", "N/A") if fields.get("priority") else "N/A"
        lines.append(f"- **[{key}]** {summary} | Status: {status} | Priority: {priority} | Assignee: {assignee_name}")

    return "\n".join(lines)


@mcp.tool()
def get_issue_comments(issue_key: str) -> str:
    """Get all comments for a Jira issue by key (e.g. ACR-84314)."""
    with _client() as client:
        resp = client.get(f"/issue/{issue_key}/comment")
        resp.raise_for_status()
        data = resp.json()

    comments = data.get("comments", [])
    if not comments:
        return f"No comments found for {issue_key}."

    lines = [f"Comments for {issue_key} ({len(comments)}):", ""]
    for comment in comments:
        lines.append(_format_comment(comment))
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
