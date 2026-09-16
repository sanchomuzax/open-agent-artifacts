from open_agent_artifacts.store import Store


def test_restore_creates_new_version_without_rewriting_history(tmp_path):
    store = Store(tmp_path / "restore.db")
    artifact = store.create_artifact("Report", "markdown", "# One", "test")
    first = store.get_current_version(artifact["id"])
    second = store.publish_version(
        artifact["id"], "# Two", "test", expected_current_version_id=first["id"], change_summary="Second",
    )

    restored = store.restore_version(
        artifact["id"], first["id"], "test", expected_current_version_id=second["id"],
    )

    assert restored["sequence"] == 3
    assert restored["content"] == "# One"
    assert [version["sequence"] for version in store.list_versions(artifact["id"])] == [3, 2, 1]


def test_rename_and_duplicate_are_explicit_mutations(tmp_path):
    store = Store(tmp_path / "mutations.db")
    artifact = store.create_artifact("Report", "text", "content", "test")

    renamed = store.rename_artifact(artifact["id"], "Renamed report")
    duplicate = store.duplicate_artifact(artifact["id"], "test")

    assert renamed["title"] == "Renamed report"
    assert renamed["id"] == artifact["id"]
    assert duplicate["id"] != artifact["id"]
    assert duplicate["title"] == "Renamed report copy"
    assert store.get_current_version(duplicate["id"])["content"] == "content"
