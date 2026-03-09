"""MCP server for Jira (Server/Data Center) via REST API v2.

Read operations: get issues, search with JQL, read comments.
Write operations: create issues, add comments, transition status, assign, link issues.
All write operations support dry-run preview (execute=False by default).
"""

import json
import os
import base64
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Jira")

JIRA_URL = os.environ.get("JIRA_URL", "").rstrip("/")
JIRA_USERNAME = os.environ.get("JIRA_USERNAME", "")
JIRA_PASSWORD = os.environ.get("JIRA_PASSWORD", "")
JIRA_DEFAULT_PROJECT = os.environ.get("JIRA_DEFAULT_PROJECT", "")
JIRA_DEFAULT_ISSUE_TYPE = os.environ.get("JIRA_DEFAULT_ISSUE_TYPE", "Task")

def _load_default_custom_fields() -> dict[str, Any]:
    raw = os.environ.get("JIRA_DEFAULT_CUSTOM_FIELDS", "")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}

JIRA_DEFAULT_CUSTOM_FIELDS: dict[str, Any] = _load_default_custom_fields()


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


# ---------------------------------------------------------------------------
# Write operations (set execute=False for preview / dry-run)
# ---------------------------------------------------------------------------


def _get_transitions(client: httpx.Client, issue_key: str) -> list[dict]:
    resp = client.get(f"/issue/{issue_key}/transitions")
    resp.raise_for_status()
    return resp.json().get("transitions", [])


def _parse_jira_error(resp: httpx.Response) -> str:
    """Extract human-readable error details from a Jira error response."""
    try:
        err = resp.json()
        messages = err.get("errorMessages", [])
        field_errors = err.get("errors", {})
        details = "; ".join(messages + [f"{k}: {v}" for k, v in field_errors.items()])
        return details or resp.text
    except Exception:
        return resp.text


@mcp.tool()
def create_issue(
    summary: str,
    project_key: str = "",
    issue_type: str = "",
    description: str = "",
    priority: str = "",
    assignee: str = "",
    labels: list[str] | None = None,
    components: list[str] | None = None,
    custom_fields: dict[str, Any] | None = None,
    execute: bool = True,
) -> str:
    """Create a new Jira issue. Set execute=False to preview without creating anything.

    Assignee is set via a separate API call after creation (many Jira projects
    do not allow setting assignee on the create screen).

    Args:
        summary: Issue title
        project_key: Project key (e.g. 'PROJ'). Uses JIRA_DEFAULT_PROJECT if not specified.
        issue_type: Issue type name. Uses JIRA_DEFAULT_ISSUE_TYPE if not specified (default 'Task').
        description: Issue description text
        priority: Priority name (e.g. 'High', 'Medium', 'Low')
        assignee: Username to assign after creation. Defaults to JIRA_USERNAME if not specified.
        labels: List of labels
        components: List of component names
        custom_fields: Dict of custom field IDs to values. Merged on top of JIRA_DEFAULT_CUSTOM_FIELDS.
        execute: True = create the issue (default), False = preview only
    """
    project_key = project_key or JIRA_DEFAULT_PROJECT
    if not project_key:
        return "Error: project_key is required (no default project configured)."

    issue_type = issue_type or JIRA_DEFAULT_ISSUE_TYPE
    assignee = assignee or JIRA_USERNAME

    merged_custom = dict(JIRA_DEFAULT_CUSTOM_FIELDS)
    if custom_fields:
        merged_custom.update(custom_fields)

    fields: dict[str, Any] = {
        "project": {"key": project_key},
        "summary": summary,
        "issuetype": {"name": issue_type},
    }
    if description:
        fields["description"] = description
    if priority:
        fields["priority"] = {"name": priority}
    if labels:
        fields["labels"] = labels
    if components:
        fields["components"] = [{"name": c} for c in components]
    if merged_custom:
        fields.update(merged_custom)

    preview_lines = [
        "## Preview: Create Issue",
        "",
        f"**Project:** {project_key}",
        f"**Type:** {issue_type}",
        f"**Summary:** {summary}",
        f"**Description:** {description or '(empty)'}",
        f"**Priority:** {priority or '(default)'}",
        f"**Assignee:** {assignee or '(unassigned)'}",
        f"**Labels:** {', '.join(labels) if labels else '(none)'}",
        f"**Components:** {', '.join(components) if components else '(none)'}",
    ]
    if merged_custom:
        preview_lines.append(f"**Custom fields:** {', '.join(merged_custom.keys())}")

    if not execute:
        preview_lines.append("")
        preview_lines.append("*This is a preview. Set `execute=True` to create the issue.*")
        return "\n".join(preview_lines)

    with _client() as client:
        resp = client.post("/issue", json={"fields": fields})
        if resp.status_code >= 400:
            return f"Error creating issue (HTTP {resp.status_code}): {_parse_jira_error(resp)}"
        data = resp.json()

    key = data.get("key", "???")
    result_lines = [
        f"Issue **{key}** created successfully.",
        f"**URL:** {JIRA_URL}/browse/{key}",
    ]

    if assignee:
        with _client() as client:
            assign_resp = client.put(f"/issue/{key}/assignee", json={"name": assignee})
            if assign_resp.status_code < 400:
                result_lines.append(f"**Assigned to:** {assignee}")
            else:
                result_lines.append(f"**Warning:** could not assign to {assignee}: {_parse_jira_error(assign_resp)}")

    result_lines.extend(["", *preview_lines])
    return "\n".join(result_lines)


@mcp.tool()
def add_comment(issue_key: str, body: str, execute: bool = True) -> str:
    """Add a comment to a Jira issue. Set execute=False to preview without posting.

    Args:
        issue_key: Issue key (e.g. 'PROJ-123')
        body: Comment text
        execute: True = post the comment (default), False = preview only
    """
    preview_lines = [
        "## Preview: Add Comment",
        "",
        f"**Issue:** {issue_key}",
        f"**Comment:**",
        "",
        body,
    ]

    if not execute:
        preview_lines.append("")
        preview_lines.append("*This is a preview. Set `execute=True` to post the comment.*")
        return "\n".join(preview_lines)

    with _client() as client:
        resp = client.post(f"/issue/{issue_key}/comment", json={"body": body})
        resp.raise_for_status()

    return "\n".join([
        f"Comment added to **{issue_key}** successfully.",
        "",
        *preview_lines,
    ])


@mcp.tool()
def transition_issue(issue_key: str, transition_name: str, execute: bool = True) -> str:
    """Change the status of a Jira issue via a workflow transition. Set execute=False to preview available transitions without performing.

    Args:
        issue_key: Issue key (e.g. 'PROJ-123')
        transition_name: Target transition name (e.g. 'In Progress', 'Done', 'Reopen')
        execute: True = perform the transition (default), False = preview only
    """
    with _client() as client:
        transitions = _get_transitions(client, issue_key)

        available = [t["name"] for t in transitions]
        match = next((t for t in transitions if t["name"].lower() == transition_name.lower()), None)

        preview_lines = [
            "## Preview: Transition Issue",
            "",
            f"**Issue:** {issue_key}",
            f"**Requested transition:** {transition_name}",
            f"**Available transitions:** {', '.join(available) if available else '(none)'}",
        ]

        if not match:
            preview_lines.append("")
            preview_lines.append(f"Error: transition '{transition_name}' not found. Use one of the available transitions listed above.")
            return "\n".join(preview_lines)

        preview_lines.append(f"**Transition ID:** {match['id']}")
        preview_lines.append(f"**Target status:** {match.get('to', {}).get('name', 'N/A')}")

        if not execute:
            preview_lines.append("")
            preview_lines.append("*This is a preview. Set `execute=True` to perform the transition.*")
            return "\n".join(preview_lines)

        resp = client.post(
            f"/issue/{issue_key}/transitions",
            json={"transition": {"id": match["id"]}},
        )
        resp.raise_for_status()

    return "\n".join([
        f"Issue **{issue_key}** transitioned to **{match.get('to', {}).get('name', transition_name)}** successfully.",
        "",
        *preview_lines,
    ])


@mcp.tool()
def assign_issue(issue_key: str, assignee: str, execute: bool = True) -> str:
    """Assign a Jira issue to a user. Set execute=False to preview without changing.

    Args:
        issue_key: Issue key (e.g. 'PROJ-123')
        assignee: Username to assign (Jira username, not display name)
        execute: True = assign the issue (default), False = preview only
    """
    preview_lines = [
        "## Preview: Assign Issue",
        "",
        f"**Issue:** {issue_key}",
        f"**New assignee:** {assignee}",
    ]

    if not execute:
        preview_lines.append("")
        preview_lines.append("*This is a preview. Set `execute=True` to assign the issue.*")
        return "\n".join(preview_lines)

    with _client() as client:
        resp = client.put(f"/issue/{issue_key}/assignee", json={"name": assignee})
        resp.raise_for_status()

    return "\n".join([
        f"Issue **{issue_key}** assigned to **{assignee}** successfully.",
        "",
        *preview_lines,
    ])


@mcp.tool()
def link_issues(
    inward_issue_key: str,
    outward_issue_key: str,
    link_type: str = "Relates",
    execute: bool = True,
) -> str:
    """Create a link between two Jira issues. Set execute=False to preview without creating.

    Args:
        inward_issue_key: The issue that is the source of the link (e.g. 'PROJ-1')
        outward_issue_key: The issue that is the target of the link (e.g. 'PROJ-2')
        link_type: Link type name (e.g. 'Blocks', 'Relates', 'Cloners', 'Duplicate')
        execute: True = create the link (default), False = preview only
    """
    preview_lines = [
        "## Preview: Link Issues",
        "",
        f"**Link type:** {link_type}",
        f"**Inward issue:** {inward_issue_key}",
        f"**Outward issue:** {outward_issue_key}",
    ]

    if not execute:
        preview_lines.append("")
        preview_lines.append("*This is a preview. Set `execute=True` to create the link.*")
        return "\n".join(preview_lines)

    with _client() as client:
        resp = client.post("/issueLink", json={
            "type": {"name": link_type},
            "inwardIssue": {"key": inward_issue_key},
            "outwardIssue": {"key": outward_issue_key},
        })
        resp.raise_for_status()

    return "\n".join([
        f"Link **{link_type}** created: {inward_issue_key} -> {outward_issue_key}.",
        "",
        *preview_lines,
    ])


if __name__ == "__main__":
    mcp.run(transport="stdio")
