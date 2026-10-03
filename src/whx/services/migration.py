"""End-to-end HoursTracker CSV migration into a WorkingHours backup."""

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from whx.services.database import compress_database, extract_database
from whx.services.hourstracker_import import ImportSummary, import_hourstracker_csv


@dataclass(frozen=True)
class MigrationSummary:
    """Final backup path and import statistics; intermediate files are temporary."""

    backup_path: Path
    imported: ImportSummary


def migrate_csv(
    source: Path, backup: Path, destination: Path | None = None
) -> MigrationSummary:
    """Extract, import, and compress without changing either input.

    Intermediate databases are removed on success and failure. Only a validated,
    uniquely named final backup is retained. Import statistics refer to the
    temporary database; callers should report backup_path as the final output.
    """
    if destination is None:
        destination = backup.with_name(f"{backup.stem}-imported.wh")
    if destination.resolve() in {source.resolve(), backup.resolve()}:
        raise ValueError(
            "Destination must be different from the CSV and source backup."
        )

    with TemporaryDirectory(prefix="whx-migrate-") as directory:
        extracted = extract_database(backup, Path(directory) / "WorkingHours.db")
        imported = import_hourstracker_csv(source, extracted)
        output = compress_database(imported.database_path, destination)

    return MigrationSummary(output, imported)
