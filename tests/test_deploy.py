from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_service_template_is_loopback_and_hardened():
    service = (ROOT / "deploy" / "open-agent-artifacts.service").read_text(encoding="utf-8")

    assert "--host 127.0.0.1" in service
    assert "NoNewPrivileges=true" in service
    assert "ProtectSystem=strict" in service
    assert "ProtectHome=true" in service
    assert "OAA_INSTANCE_ID=production" in service
    assert "OAA_STORAGE_CLASS=persistent" in service
    assert "Funnel" not in service
    assert "/home/" not in service
