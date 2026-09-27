import pytest
from src.mcp_server.server import app


@pytest.mark.anyio
async def test_mcp_server_tools_registered():
    """Verify that all core tools are properly registered on the MCP server."""
    tools = await app.list_tools()
    tool_names = {t.name for t in tools}

    expected_tools = {
        "execute_readonly_sql",
        "semantic_vector_search",
        "get_table_schema",
        "ingest_github_issues",
        "index_embeddings",
    }
    assert expected_tools.issubset(tool_names)


@pytest.mark.anyio
async def test_mcp_server_resources_registered():
    """Verify that MCP resources and resource templates are registered."""
    resources = await app.list_resources()
    resource_uris = {str(r.uri) for r in resources}
    assert "techradar://repositories" in resource_uris
    assert "techradar://stats" in resource_uris

    templates = await app.list_resource_templates()
    template_uris = {str(tpl.uri_template) for tpl in templates}
    assert "techradar://schema/{table_name}" in template_uris
    assert "techradar://issues/{owner}/{repo_name}" in template_uris


@pytest.mark.anyio
async def test_mcp_server_prompts_registered():
    """Verify that prompt templates are properly registered on the MCP server."""
    prompts = await app.list_prompts()
    prompt_names = {p.name for p in prompts}
    assert "query_radar" in prompt_names
    assert "triage_issue" in prompt_names


@pytest.mark.anyio
async def test_execute_readonly_sql_safety():
    """Verify that destructive SQL statements are rejected by execute_readonly_sql."""
    # Test non-SELECT / non-WITH query
    result = await app.call_tool("execute_readonly_sql", {"query": "DROP TABLE raw_github_events;"})
    content = result.content[0].text
    assert "Error: Only SELECT or WITH queries are permitted." in content

    # Test destructive command inside query
    result2 = await app.call_tool(
        "execute_readonly_sql",
        {"query": "SELECT * FROM raw_github_events; DELETE FROM raw_github_events;"},
    )
    content2 = result2.content[0].text
    assert "Error: Destructive or data-modifying SQL statements are forbidden." in content2


@pytest.mark.anyio
async def test_query_radar_prompt_rendering():
    """Verify prompt rendering for query_radar."""
    rendered = await app.get_prompt("query_radar", {"question": "Check memory issues in DuckDB"})
    assert rendered is not None
    assert len(rendered.messages) > 0
    message_text = rendered.messages[0].content.text
    assert "Check memory issues in DuckDB" in message_text
    assert "execute_readonly_sql" in message_text
    assert "semantic_vector_search" in message_text


@pytest.mark.anyio
async def test_triage_issue_prompt_rendering():
    """Verify prompt rendering for triage_issue."""
    rendered = await app.get_prompt(
        "triage_issue",
        {"issue_title": "Memory leak on join", "issue_body": "Causes OOM when joining large tables"},
    )
    assert rendered is not None
    message_text = rendered.messages[0].content.text
    assert "Memory leak on join" in message_text
    assert "Causes OOM when joining large tables" in message_text
    assert "Critical Bug/Crash" in message_text


@pytest.mark.anyio
async def test_read_repositories_resource():
    """Verify reading the repositories resource."""
    res = await app.read_resource("techradar://repositories")
    assert res is not None
    assert len(res) > 0
    content = res[0].content
    assert "duckdb/duckdb" in content
    assert "pola-rs/polars" in content
    assert "pydantic/pydantic" in content
