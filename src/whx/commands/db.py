"""Commands for WorkingHours SQLite databases."""

import sqlite3
from pathlib import Path

import click

from whx.services.database import compress_database, extract_database
from whx.services.hourstracker_import import ImportSummary, import_hourstracker_csv
from whx.services.migration import migrate_csv


@click.group()
def db() -> None:
    """Extract backups, import time entries, compress databases, or migrate."""


@db.command()
@click.argument(
    "source",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("data/WorkingHours.wh"),
)
@click.argument(
    "destination",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("data/WorkingHours.db"),
)
def extract(source: Path, destination: Path) -> None:
    """Extract a compressed backup to a SQLite database file.

    SOURCE defaults to data/WorkingHours.wh. DESTINATION defaults to
    data/WorkingHours.db. Existing files are preserved using a numeric suffix.
    """
    try:
        output_path = extract_database(source, destination)
    except (ValueError, OSError, EOFError, sqlite3.Error) as error:
        raise click.ClickException(str(error)) from error

    click.echo(f"Extracted {source} to {output_path}")


@db.command()
@click.argument(
    "source",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.argument(
    "destination",
    type=click.Path(dir_okay=False, path_type=Path),
    required=False,
)
def compress(source: Path, destination: Path | None) -> None:
    """Compress a SQLite database into a new gzip .wh backup.

    DESTINATION defaults to SOURCE with a .wh suffix. Existing files are
    preserved using a numeric suffix. The source database is not modified.
    Keep your original backup; app restore compatibility is not guaranteed.
    """
    try:
        output_path = compress_database(source, destination)
    except (ValueError, OSError, sqlite3.Error) as error:
        raise click.ClickException(str(error)) from error

    click.echo(f"Compressed {source} to {output_path}")
    click.echo(
        "Keep the original backup before restoring; SQLite integrity does not "
        "guarantee WorkingHours restore compatibility.",
        err=True,
    )


@db.command("import")
@click.option(
    "--file-type",
    type=click.Choice(["csv"], case_sensitive=False),
    default="csv",
    show_default=True,
    help="Source file format.",
)
@click.argument(
    "source",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.argument(
    "database",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
def import_csv(source: Path, database: Path, file_type: str) -> None:
    """Import an HoursTracker CSV into a new copy of a WorkingHours database.

    Neither input is modified. A unique DATABASE-imported filename is chosen;
    records are added transactionally. Keep the original WorkingHours backup.
    """
    try:
        summary = import_hourstracker_csv(source, database)
    except (ValueError, OSError, sqlite3.Error) as error:
        raise click.ClickException(str(error)) from error

    _report_import(summary, summary.database_path)


def _report_import(summary: ImportSummary, output_path: Path) -> None:
    click.echo(
        f"Imported {summary.work_units_added} work units and "
        f"{summary.bonus_work_units_added} zero-duration bonus work units "
        f"into {output_path}; created {summary.projects_created} projects "
        f"and {summary.tags_created} tags."
    )
    if summary.mileage_values_ignored:
        click.echo(
            f"Ignored {summary.mileage_values_ignored} mileage values as requested; "
            "the source CSV is unchanged.",
            err=True,
        )
    if summary.source_only_fields:
        fields = ", ".join(
            f"{name} ({count} records)" for name, count in summary.source_only_fields
        )
        click.echo(
            "These non-empty CSV fields are retained only in the unchanged source "
            f"file: {fields}.",
            err=True,
        )


@db.command("migrate")
@click.option(
    "--file-type",
    type=click.Choice(["csv"], case_sensitive=False),
    default="csv",
    show_default=True,
    help="Source file format.",
)
@click.argument(
    "source",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.argument(
    "backup",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.argument(
    "destination",
    type=click.Path(dir_okay=False, path_type=Path),
    required=False,
)
def migrate(
    source: Path, backup: Path, destination: Path | None, file_type: str
) -> None:
    """Extract BACKUP, import SOURCE CSV, and compress a new .wh backup.

    DESTINATION defaults to BACKUP-imported.wh; existing outputs get numeric
    suffixes. Inputs are unchanged and intermediate databases are temporary.
    """
    try:
        summary = migrate_csv(source, backup, destination)
    except (ValueError, OSError, EOFError, sqlite3.Error) as error:
        raise click.ClickException(str(error)) from error

    _report_import(summary.imported, summary.backup_path)
    click.echo(
        "Keep the original backup before restoring; SQLite integrity does not "
        "guarantee WorkingHours restore compatibility.",
        err=True,
    )
