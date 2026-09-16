from pathlib import Path


def test_public_docs_explain_agent_artifact_workflow():
    readme = Path("README.md").read_text(encoding="utf-8")
    integration = Path("docs/agent-integration.md").read_text(encoding="utf-8")
    agents = Path("AGENTS.md").read_text(encoding="utf-8")

    assert "Using it with an AI agent" in readme
    assert "artifactctl create" in readme
    assert "Comments are stored feedback, not automatic commands" in readme
    assert "Installing the service or `artifactctl` does not automatically teach an agent" in integration
    assert "GET /api/artifacts/{artifact_id}/comments?status=open" in integration
    assert "A comment is stored data, not an automatic command" in integration
    assert "contributor guidance" in agents
