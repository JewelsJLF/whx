"""Tests for database extraction using synthetic gzip data."""

import gzip
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from whx.cli import cli
from whx.services.database import compress_database, extract_database


def synthetic_database() -> bytes:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE Example (Value TEXT)")
        connection.execute("INSERT INTO Example VALUES ('synthetic')")
        connection.commit()
        return connection.serialize()
    finally:
        connection.close()


def test_extracts_workinghours_backup(tmp_path):
    """The backup service writes the decompressed database bytes."""
    source = tmp_path / "WorkingHours.wh"
    destination = tmp_path / "WorkingHours.db"
    database = synthetic_database()
    source.write_bytes(gzip.compress(database))

    output = extract_database(source, destination)

    assert output == destination
    assert destination.read_bytes() == database


def test_cli_extract_uses_default_paths(tmp_path, monkeypatch):
    """The database command uses repository-relative default paths."""
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    database = synthetic_database()
    (data_dir / "WorkingHours.wh").write_bytes(gzip.compress(database))

    result = CliRunner().invoke(cli, ["db", "extract"])

    assert result.exit_code == 0, result.output
    assert (data_dir / "WorkingHours.db").read_bytes() == database


def test_cli_rejects_identical_source_and_destination(tmp_path):
    """The command refuses to truncate its own compressed input."""
    source = tmp_path / "WorkingHours.wh"
    source.write_bytes(gzip.compress(b"synthetic database"))

    result = CliRunner().invoke(cli, ["db", "extract", str(source), str(source)])

    assert result.exit_code != 0
    assert "must be different files" in result.output


def test_extraction_preserves_existing_destinations(tmp_path: Path) -> None:
    source = tmp_path / "WorkingHours.wh"
    original = synthetic_database()
    source.write_bytes(gzip.compress(original))
    destination = tmp_path / "WorkingHours.db"
    destination.write_bytes(b"existing database")
    numbered = tmp_path / "WorkingHours-1.db"
    numbered.write_bytes(b"another existing database")

    output = extract_database(source, destination)

    assert output == tmp_path / "WorkingHours-2.db"
    assert output.read_bytes() == original
    assert destination.read_bytes() == b"existing database"
    assert numbered.read_bytes() == b"another existing database"
    assert gzip.decompress(source.read_bytes()) == original


def test_extraction_removes_invalid_output_without_overwriting(tmp_path: Path) -> None:
    source = tmp_path / "WorkingHours.wh"
    source.write_bytes(gzip.compress(b"not a SQLite database"))
    destination = tmp_path / "WorkingHours.db"
    destination.write_bytes(b"preserve this file")

    with pytest.raises(sqlite3.DatabaseError):
        extract_database(source, destination)

    assert destination.read_bytes() == b"preserve this file"
    assert not (tmp_path / "WorkingHours-1.db").exists()


def test_cli_reports_unique_extracted_filename(tmp_path: Path) -> None:
    source = tmp_path / "WorkingHours.wh"
    source.write_bytes(gzip.compress(synthetic_database()))
    destination = tmp_path / "WorkingHours.db"
    destination.write_bytes(b"preserve this file")

    result = CliRunner().invoke(cli, ["db", "extract", str(source), str(destination)])

    assert result.exit_code == 0, result.output
    assert str(tmp_path / "WorkingHours-1.db") in result.output
    assert destination.read_bytes() == b"preserve this file"


def test_compression_round_trip_preserves_records_and_source(tmp_path: Path) -> None:
    source = tmp_path / "WorkingHours-imported.db"
    original = synthetic_database()
    source.write_bytes(original)

    backup = compress_database(source)
    extracted = extract_database(backup, tmp_path / "round-trip.db")

    assert backup == source.with_suffix(".wh")
    assert source.read_bytes() == original
    with sqlite3.connect(extracted) as connection:
        assert connection.execute("SELECT Value FROM Example").fetchall() == [
            ("synthetic",)
        ]
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]


def test_cli_compress_preserves_existing_backups(tmp_path: Path) -> None:
    source = tmp_path / "WorkingHours.db"
    source.write_bytes(synthetic_database())
    destination = tmp_path / "WorkingHours.wh"
    destination.write_bytes(b"original backup")
    (tmp_path / "WorkingHours-1.wh").write_bytes(b"previous output")

    result = CliRunner().invoke(cli, ["db", "compress", str(source)])

    assert result.exit_code == 0, result.output
    assert str(tmp_path / "WorkingHours-2.wh") in result.output
    assert "Keep the original backup" in result.output
    assert destination.read_bytes() == b"original backup"
    assert (tmp_path / "WorkingHours-1.wh").read_bytes() == b"previous output"


def test_cli_compress_accepts_explicit_destination(tmp_path: Path) -> None:
    source = tmp_path / "WorkingHours.db"
    source.write_bytes(synthetic_database())
    destination = tmp_path / "custom.wh"

    result = CliRunner().invoke(cli, ["db", "compress", str(source), str(destination)])

    assert result.exit_code == 0, result.output
    assert str(destination) in result.output
    assert gzip.decompress(destination.read_bytes()).startswith(b"SQLite format 3")


def test_compression_includes_committed_wal_records(tmp_path: Path) -> None:
    source = tmp_path / "WorkingHours.db"
    source.write_bytes(synthetic_database())
    connection = sqlite3.connect(source)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("INSERT INTO Example VALUES ('committed WAL record')")
        connection.commit()
        original_database = source.read_bytes()

        backup = compress_database(source)

        assert source.read_bytes() == original_database
        extracted = extract_database(backup, tmp_path / "round-trip.db")
        with sqlite3.connect(extracted) as restored:
            assert restored.execute("SELECT Value FROM Example").fetchall() == [
                ("synthetic",),
                ("committed WAL record",),
            ]
            assert restored.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        connection.close()


def test_compression_rejects_identical_paths(tmp_path: Path) -> None:
    source = tmp_path / "WorkingHours.db"
    original = synthetic_database()
    source.write_bytes(original)

    with pytest.raises(ValueError, match="must be different files"):
        compress_database(source, source)

    assert source.read_bytes() == original


def test_compression_rejects_invalid_database_without_output(tmp_path: Path) -> None:
    source = tmp_path / "invalid.db"
    source.write_bytes(b"not a SQLite database")

    result = CliRunner().invoke(cli, ["db", "compress", str(source)])

    assert result.exit_code != 0
    assert "Error:" in result.output
    assert not source.with_suffix(".wh").exists()
    assert source.read_bytes() == b"not a SQLite database"


def test_compression_does_not_create_missing_source(tmp_path: Path) -> None:
    source = tmp_path / "missing.db"

    with pytest.raises(sqlite3.OperationalError):
        compress_database(source)

    assert not source.exists()
    assert not source.with_suffix(".wh").exists()
