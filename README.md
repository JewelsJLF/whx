# WHX — WorkingHours Exchange

## Overview

WHX is a command-line migration tool that imports HoursTracker CSV time entries into WorkingHours backups. It combines backup extraction, CSV import, and backup compression in a single command, or lets you run each step separately. Source files and existing outputs are preserved.

The importer targets the schema observed in WorkingHours 2.17.9.0. Some HoursTracker fields are reported but retained only in the unchanged source CSV. Generating a `.wh` backup does not establish restore compatibility across app versions.

## Requirements

- Python 3.14 or newer
- A WorkingHours `.wh` backup, placed at `data/WorkingHours.wh`

The command-line app depends on Click. Development tools are available through the `dev` extra.

Install the app in a virtual environment:

```powershell
python -m pip install whx
```

To install from a source checkout with development tools:

```powershell
python -m pip install -e ".[dev]"
pre-commit install
```

Pre-commit runs whitespace/config checks, Ruff linting/formatting, mypy type checking, and Bandit security checks on commits. Markdown is not subject to an enforced line length or automatic reflow; prose may wrap naturally for source readability. To run all hooks against the repository manually:

```powershell
pre-commit run --all-files
```

## Extract a backup

After installing the project, use the `whx` command. With no arguments, `whx db extract` reads `data/WorkingHours.wh` and writes `data/WorkingHours.db` relative to the current working directory:

```powershell
whx db extract
```

You can also provide the source backup and destination database paths:

```powershell
whx db extract path\to\backup.wh path\to\WorkingHours.db
```

The source and existing destinations are never modified. If the destination exists, extraction chooses a numeric suffix such as `WorkingHours-1.db`, then `WorkingHours-2.db`, and reports the actual filename. Source and destination must be different paths, and the destination parent directory must already exist. Extracted outputs must pass SQLite integrity checks; failed outputs are removed.

## Import an HoursTracker CSV

After extracting a backup, import a CSV into the extracted database:

```powershell
whx db import data\CSVExport.csv data\WorkingHours.db
```

The command creates a SQLite backup copy, adds records transactionally, verifies SQLite integrity, and leaves both the CSV and input database unchanged. The output is named `WorkingHours-imported.db`, or `WorkingHours-imported-1.db`, `WorkingHours-imported-2.db`, etc. if previous outputs exist. The command reports the actual output path; failed outputs are removed. It creates or reuses projects for each `Job` and hourly-rate pair, using the default WorkingHours project's currency for new projects. Project names append letters (`A`, `B`, etc.) to the source job name in order of each rate's earliest work-unit date; the hourly rate is stored on the project, not exposed in its name. New projects, including lump-sum bonus projects, receive distinct coordinated colors instead of copying the default or parent project's color; existing projects retain their colors. CSV tags are added as ordinary work-unit tags. Positive earnings adjustments are represented as separate lump-sum projects with zero-duration work units, so they do not add time worked. Existing matching work units in the input database cause the import to stop rather than duplicate them.

The importer stores CSV clock-in, clock-out, and already-adjusted duration values directly; it does not reapply `TotalTimeAdjustment`. A non-negative difference between elapsed time and duration indicates aggregate excluded time, but individual break intervals remain only in the unchanged CSV. Mileage is ignored. Other non-empty fields that are not mapped are listed in the command output and retained only in the source CSV.

Zero-duration entries are preserved, including entries with identical clock-in and clock-out times. Equal timestamps require zero duration; clock-out earlier than clock-in is rejected.

New tasks and tags receive distinct opaque colors from expanding coordinated palettes with distributed hues and balanced saturation/brightness. Task colors avoid all colors already assigned to projects, case-insensitively, and other task colors generated during the import; tag colors continue to avoid existing and newly generated tag colors. Existing task and tag colors are preserved. A large number of unique colors is not a guarantee that every pair will be visually distinguishable.

Use the reported new database for subsequent compression; the selected input database is not modified. Repeating an import with the same input produces another independent output, not an update to an earlier output. Keep the original WorkingHours backup and a known-good copy of the database. The importer requires the schema columns, `user_version`, and `MigrationKey` observed in WorkingHours 2.17.9.0, but cannot establish that every app release can restore a modified database. Do not attempt to restore a raw `.db` file as a `.wh` backup: `.wh` files are gzip-compressed SQLite databases.

## Extract, import, and compress in one command

```powershell
whx db migrate data\CSVExport.csv data\WorkingHours.wh
```

This runs all three steps and creates `data\WorkingHours-imported.wh`, choosing a numeric suffix if the output already exists. An optional third argument specifies the destination backup path. Both inputs and existing outputs are preserved. Intermediate databases are temporary and removed on success or failure; only the final backup is retained. The command reports the output path, import counts, and unmapped CSV fields just like `import`.

Both `import` and `migrate` accept `--file-type csv`, which is the default and only supported format.

Keep the original backup and CSV. The same schema, mapping, and restore compatibility limitations apply as for the individual commands; the combined command does not restore the backup into WorkingHours.

## Compress a database into a backup

Use the actual database path reported by import (which may have a numeric suffix):

```powershell
whx db compress data\WorkingHours-imported.db
```

The default output is the source filename with a `.wh` extension, such as `data\WorkingHours-imported.wh`. You can also choose a destination:

```powershell
whx db compress data\WorkingHours-imported.db data\restore-candidate.wh
```

Existing backups are never overwritten: the command chooses `restore-candidate-1.wh`, `restore-candidate-2.wh`, etc. on collisions and reports the actual path. It opens the database read-only, creates a consistent SQLite snapshot including committed WAL data, validates it with `PRAGMA integrity_check`, and gzip-compresses the snapshot. The source database is unchanged.

Keep the original backup before attempting a restore. A valid SQLite database in the original gzip container format does not guarantee that WorkingHours will accept its schema or imported records. App-level restore and earnings behavior still require verification in the target version.

## Data and privacy

Files in `data/` are ignored by Git because app backups and CSV exports can contain private information. Keep personal exports, extracted databases, and any derived files there (or outside the repository); do not force-add them.

## Development

### Package versions

Package versions are derived from Git tags by `setuptools-scm`, using version tags such as `v0.1.0`. This repository does not automatically publish packages to PyPI when a GitHub release is published.

### Package layout

The `whx db` command group contains backup extraction, CSV import, backup compression, and combined migration. Click is used for the command interface; file processing uses the standard library. Application code lives in `src/whx/`, with CLI commands in `src/whx/commands/`, services in `src/whx/services/`, and tests in `tests/`. Migration tests use small synthetic CSV/database fixtures, never personal exports.

See [`docs/architecture.md`](docs/architecture.md) for the current package layout and implemented backup workflow.

For development and contribution guidance, see [`CONTRIBUTING.md`](CONTRIBUTING.md). To report a security issue privately, follow [`SECURITY.md`](SECURITY.md).
