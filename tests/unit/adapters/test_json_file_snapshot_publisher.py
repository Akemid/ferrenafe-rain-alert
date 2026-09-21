"""The local file snapshot publisher (public status page)."""

from __future__ import annotations

import json
from pathlib import Path

from rain_alert.adapters.local.json_file_snapshot_publisher import JsonFileSnapshotPublisher


def test_the_document_is_written_as_utf8_json(tmp_path: Path) -> None:
    target = tmp_path / "status.json"

    JsonFileSnapshotPublisher(target).publish({"city": "Ferreñafe", "level": "none"})

    assert json.loads(target.read_text(encoding="utf-8")) == {"city": "Ferreñafe", "level": "none"}


def test_a_missing_parent_directory_is_created(tmp_path: Path) -> None:
    """An operator pointing this at a fresh path should not have to mkdir first."""
    target = tmp_path / "out" / "status.json"

    JsonFileSnapshotPublisher(target).publish({"level": "none"})

    assert target.is_file()


def test_publishing_twice_replaces_rather_than_appends(tmp_path: Path) -> None:
    target = tmp_path / "status.json"
    publisher = JsonFileSnapshotPublisher(target)

    publisher.publish({"level": "none"})
    publisher.publish({"level": "prepare"})

    assert json.loads(target.read_text(encoding="utf-8")) == {"level": "prepare"}
