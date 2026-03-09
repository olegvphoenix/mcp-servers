"""MCP server for reading Confluence pages (Server/Data Center) via REST API."""

import os
import base64
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Confluence")

CONFLUENCE_URL = os.environ.get("CONFLUENCE_URL", "").rstrip("/")
CONFLUENCE_USERNAME = os.environ.get("CONFLUENCE_USERNAME", "") or os.environ.get("MCP_USERNAME", "")
CONFLUENCE_PASSWORD = os.environ.get("CONFLUENCE_PASSWORD", "") or os.environ.get("MCP_PASSWORD", "")


def _auth_header() -> str:
    token = base64.b64encode(f"{CONFLUENCE_USERNAME}:{CONFLUENCE_PASSWORD}".encode()).decode()
    return f"Basic {token}"


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=f"{CONFLUENCE_URL}/rest/api",
        headers={
            "Authorization": _auth_header(),
            "Content-Type": "application/json",
        },
        verify=False,
        timeout=30.0,
    )


def _html_to_text(html: str) -> str:
    """Naive HTML-to-text: strip tags, decode common entities."""
    import re
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"</(p|div|tr|li|h[1-6])>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    entities = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&nbsp;": " ", "&#39;": "'"}
    for ent, char in entities.items():
        text = text.replace(ent, char)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _format_page(page: dict[str, Any], include_body: bool = True) -> str:
    title = page.get("title", "N/A")
    page_id = page.get("id", "???")
    space = page.get("space", {}).get("key", "N/A")
    version = page.get("version", {}).get("number", "N/A")
    by = page.get("version", {}).get("by", {}).get("displayName", "N/A")
    when = page.get("version", {}).get("when", "N/A")
    link = f"{CONFLUENCE_URL}{page.get('_links', {}).get('webui', '')}"

    lines = [
        f"# {title}",
        "",
        f"**Space:** {space}",
        f"**Page ID:** {page_id}",
        f"**Version:** {version}",
        f"**Last modified by:** {by}",
        f"**Last modified:** {when}",
        f"**URL:** {link}",
    ]

    if include_body:
        body_html = page.get("body", {}).get("storage", {}).get("value", "")
        if body_html:
            lines.extend(["", "## Content", "", _html_to_text(body_html)])
        else:
            lines.extend(["", "## Content", "", "(empty page)"])

    return "\n".join(lines)


@mcp.tool()
def get_page(page_id: str) -> str:
    """Get a Confluence page by its numeric ID. Returns title, space, version info, and full page content as text."""
    with _client() as client:
        resp = client.get(
            f"/content/{page_id}",
            params={"expand": "body.storage,version,space"},
        )
        resp.raise_for_status()
        page = resp.json()
    return _format_page(page)


@mcp.tool()
def get_page_by_title(space_key: str, title: str) -> str:
    """Get a Confluence page by space key and exact title. Example: space_key='DEV', title='Architecture Overview'."""
    with _client() as client:
        resp = client.get(
            "/content",
            params={
                "spaceKey": space_key,
                "title": title,
                "expand": "body.storage,version,space",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results", [])
    if not results:
        return f"No page found with title '{title}' in space '{space_key}'."
    return _format_page(results[0])


@mcp.tool()
def search_pages(cql: str, max_results: int = 10) -> str:
    """Search Confluence using CQL (Confluence Query Language).
    Examples:
      - 'space = DEV AND title ~ "architecture"'
      - 'text ~ "deployment guide"'
      - 'label = "api" AND space = TEAM'
      - 'type = page AND lastModified > now("-7d")'
    Returns up to max_results pages (default 10)."""
    max_results = min(max_results, 50)
    with _client() as client:
        resp = client.get(
            "/content/search",
            params={"cql": cql, "limit": max_results, "expand": "version,space"},
        )
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results", [])
    total = data.get("totalSize", data.get("size", 0))

    if not results:
        return f"No pages found for CQL: {cql}"

    lines = [f"Found {total} page(s) (showing {len(results)}):", ""]
    for page in results:
        title = page.get("title", "N/A")
        page_id = page.get("id", "???")
        space = page.get("space", {}).get("key", "N/A")
        modified = page.get("version", {}).get("when", "N/A")
        link = f"{CONFLUENCE_URL}{page.get('_links', {}).get('webui', '')}"
        lines.append(f"- **[{page_id}]** {title} | Space: {space} | Modified: {modified} | {link}")

    return "\n".join(lines)


@mcp.tool()
def get_page_children(page_id: str, max_results: int = 25) -> str:
    """Get child pages of a given page by its numeric ID. Useful for navigating page trees."""
    max_results = min(max_results, 100)
    with _client() as client:
        resp = client.get(
            f"/content/{page_id}/child/page",
            params={"limit": max_results, "expand": "version,space"},
        )
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results", [])
    if not results:
        return f"No child pages found for page {page_id}."

    lines = [f"Child pages of {page_id} ({len(results)}):", ""]
    for page in results:
        title = page.get("title", "N/A")
        pid = page.get("id", "???")
        modified = page.get("version", {}).get("when", "N/A")
        lines.append(f"- **[{pid}]** {title} | Modified: {modified}")

    return "\n".join(lines)


@mcp.tool()
def list_spaces(max_results: int = 50) -> str:
    """List all available Confluence spaces. Returns space key, name, and type."""
    max_results = min(max_results, 200)
    with _client() as client:
        resp = client.get("/space", params={"limit": max_results})
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results", [])
    if not results:
        return "No spaces found."

    lines = [f"Confluence spaces ({len(results)}):", ""]
    for space in results:
        key = space.get("key", "???")
        name = space.get("name", "N/A")
        stype = space.get("type", "N/A")
        lines.append(f"- **{key}** — {name} ({stype})")

    return "\n".join(lines)


@mcp.tool()
def get_page_comments(page_id: str, max_results: int = 25) -> str:
    """Get comments on a Confluence page by its numeric ID."""
    max_results = min(max_results, 100)
    with _client() as client:
        resp = client.get(
            f"/content/{page_id}/child/comment",
            params={"limit": max_results, "expand": "body.storage,version"},
        )
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results", [])
    if not results:
        return f"No comments found for page {page_id}."

    lines = [f"Comments for page {page_id} ({len(results)}):", ""]
    for comment in results:
        author = comment.get("version", {}).get("by", {}).get("displayName", "N/A")
        when = comment.get("version", {}).get("when", "N/A")
        body_html = comment.get("body", {}).get("storage", {}).get("value", "")
        body = _html_to_text(body_html) if body_html else "(empty)"
        lines.extend([f"**{author}** ({when}):", body, "", "---", ""])

    return "\n".join(lines)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
