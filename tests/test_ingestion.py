import json
from src.ingestion.github_poller import GitHubIssuePoller


def test_parse_issue():
    poller = GitHubIssuePoller(token="dummy_token")
    dummy_payload = {
        "id": 1234567,
        "number": 42,
        "title": "v2.8 memory spike when parsing nested models",
        "user": {"login": "octocat"},
        "state": "open",
        "labels": [{"name": "bug"}, {"name": "performance"}],
        "body": "Memory increases rapidly when using recursive BaseModel definitions.",
        "comments": 3,
        "created_at": "2024-03-01T10:00:00Z",
        "updated_at": "2024-03-01T11:00:00Z",
    }

    parsed = poller.parse_issue("pydantic/pydantic", dummy_payload)

    assert parsed["repo"] == "pydantic/pydantic"
    assert parsed["event_type"] == "issue"
    assert parsed["issue_number"] == 42
    assert parsed["title"] == "v2.8 memory spike when parsing nested models"
    assert parsed["author"] == "octocat"
    assert parsed["state"] == "open"
    assert json.loads(parsed["labels"]) == ["bug", "performance"]
    assert parsed["comments_count"] == 3
    assert parsed["upstream_created_at"] == "2024-03-01T10:00:00Z"
    assert "Memory increases" in parsed["body"]
