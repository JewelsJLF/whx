"""Services for extracting and compressing WorkingHours backups."""

import gzip
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO


@contextmanager
def unique_output(destination: Path) -> Iterator[tuple[Path, BinaryIO]]:
    """Exclusively reserve an output path, removing incomplete files on failure."""
    index = 0
    while True:
        candidate = (
            destination
            if index == 0
            else destination.with_name(
                f"{destination.stem}-{index}{destination.suffix}"
            )
        )
        try:
            output = candidate.open("xb")
        except FileExistsError:
            index += 1
            continue
        break

    try:
        with output:
            yield candidate, output
    except BaseException:
        candidate.unlink()
        raise


def check_integrity(connection: sqlite3.Connection) -> None:
    """Reject SQLite databases that fail an integrity check."""
    results = connection.execute("PRAGMA integrity_check").fetchall()
    if results != [("ok",)]:
        raise ValueError(f"SQLite integrity_check failed: {results!r}")


@contextmanager
def database_copy(database: Path) -> Iterator[tuple[Path, sqlite3.Connection]]:
    """Back up a read-only SQLite source into a unique output for modification."""
    destination = database.with_name(f"{database.stem}-imported{database.suffix}")
    with unique_output(destination) as (output_path, output):
        output.close()
        with closing(
            sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
        ) as source:
            check_integrity(source)
            with closing(sqlite3.connect(output_path)) as connection:
                source.backup(connection)
                yield output_path, connection


def extract_database(source: Path, destination: Path) -> Path:
    """Decompress a WorkingHours backup into a database file.

    The gzip-compressed backup is the input; the SQLite database is the
    output.
    The source is never modified. Existing destinations are preserved by adding
    a numeric suffix to the output filename. The parent directory must exist.
    Return the actual output path after validating SQLite integrity.

    Raises:
        ValueError: If source and destination resolve to the same file.
    """
    if source.resolve() == destination.resolve():
        raise ValueError("Source and destination must be different files.")

    with unique_output(destination) as (output_path, f_out):
        with gzip.open(source, "rb") as f_in:
            shutil.copyfileobj(f_in, f_out)
        f_out.close()
        with closing(
            sqlite3.connect(output_path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as connection:
            check_integrity(connection)
    return output_path


def compress_database(source: Path, destination: Path | None = None) -> Path:
    """Compress a validated SQLite snapshot into a new gzip .wh backup.

    Include committed WAL data without modifying the source. Default to the
    source filename with a .wh suffix and preserve existing destinations.
    """
    if destination is None:
        destination = source.with_suffix(".wh")
    if source.resolve() == destination.resolve():
        raise ValueError("Source and destination must be different files.")

    with TemporaryDirectory(prefix="whx-compress-") as temporary_directory:
        snapshot_path = Path(temporary_directory) / "snapshot.db"
        with closing(
            sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
        ) as source_connection:
            with closing(sqlite3.connect(snapshot_path)) as snapshot:
                source_connection.backup(snapshot)
                snapshot.execute("PRAGMA journal_mode=DELETE")
                check_integrity(snapshot)

        with unique_output(destination) as (output_path, output):
            with snapshot_path.open("rb") as snapshot_file:
                with gzip.GzipFile(
                    filename="", mode="wb", fileobj=output, mtime=0
                ) as compressed:
                    shutil.copyfileobj(snapshot_file, compressed)

    return output_path
