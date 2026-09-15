from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_public_hermes_contract_routes_every_artifact_to_workspace():
    contract = (ROOT / "integrations" / "hermes" / "SKILL.md").read_text(encoding="utf-8")

    assert "Every artifact request" in contract
    assert "GitHub is the source-code extra" in contract
    assert "returned artifact ID" in contract
