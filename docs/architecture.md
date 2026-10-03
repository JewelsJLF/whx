# WHX migration architecture

WHX (WorkingHours Exchange) is a Python 3.14+ CLI for migrating HoursTracker CSV time entries into WorkingHours backups. The PyPI distribution, Python package, and console command are all named `whx`.

## Package layout

```text
src/whx/
├── cli.py                 # Top-level Click command group
├── commands/              # CLI command groups and argument handling
└── services/              # Reusable application operations
tests/                     # Synthetic-data tests
```

Commands should validate CLI input, call services for application behavior, and translate expected service errors into user-facing CLI errors. Keep filesystem or database operations out of Click callback code when they can live in a reusable service.

## Backup extraction

`whx db extract [SOURCE] [DESTINATION]` decompresses a gzip-compressed WorkingHours `.wh` backup into a database file:

- `SOURCE` defaults to `data/WorkingHours.wh`.
- `DESTINATION` defaults to `data/WorkingHours.db`.
- Paths are resolved from the current working directory.
- Existing destinations are preserved using a numeric suffix (`WorkingHours-1.db`, `WorkingHours-2.db`, etc.); the parent directory must already exist.
- Outputs must pass SQLite integrity checks; incomplete or invalid outputs are removed.
- Source and destination must not resolve to the same file. The source backup is not modified.

The operation is implemented by `whx.services.database.extract_database` and exposed by the `db extract` Click command. It belongs to the `db` command group because the operation produces a SQLite database; the compressed backup is the input format, not the operation performed.

## Backup compression

`whx db compress SOURCE [DESTINATION]` calls `whx.services.database.compress_database` to create a gzip-compressed SQLite `.wh` backup. The destination defaults to the source filename with a `.wh` suffix. Existing files are preserved through exclusive output reservation and numeric suffixes; the command reports the actual output path.

The service opens the source read-only and uses SQLite's backup API to make a temporary snapshot, including committed WAL data. The snapshot is switched to rollback-journal mode so the compressed database is standalone, then validated with `PRAGMA integrity_check`. Compression streams the snapshot into gzip without embedding a temporary filename or current timestamp. Temporary snapshots are cleaned up, and incomplete outputs are removed on failure. The source database and original backups are not modified. Matching source and destination paths are rejected.

Container-format round trips are covered by synthetic tests. App-level restoration and schema compatibility are not established by compression or SQLite integrity checks.

## HoursTracker CSV parsing

`whx.services.hourstracker_csv.read_hourstracker_csv` reads CSV headers and records as strings, preserving every column and blank field for later mapping. It rejects empty or duplicate headers and rows with a field count that does not match the header. Parsing does not write to a database or infer field meanings; the import service validates and maps supported values separately.

The parser is tested with synthetic data in `tests/fixtures/hourstracker_export.csv`; never use a personal export as a test fixture.

`whx.services.hourstracker_import.import_hourstracker_csv` opens its input database read-only and uses SQLite's backup API to create a new copy, including committed data in a WAL. It writes supported fields to that copy in one transaction. Outputs use an `-imported` suffix, with numeric suffixes on collisions. It validates the required table/column signature and checks `PRAGMA integrity_check` before and after writing. Neither input is modified, and exact matching work units in the input are rejected to prevent duplicate imports.

Shared database services reserve outputs with exclusive file creation rather than an existence-check followed by overwrite. On failure, only the newly reserved output is removed. Extraction returns its actual output path; import exposes it as `ImportSummary.database_path`, and both CLI commands report it.

The supported schema was inspected from a synthetic WorkingHours 2.17.9.0 database and includes `Projects`, `ProjectTags`, `Settings`, `Tags`, `WorkUnits`, and `WorkUnitTags`. The import gate requires the observed columns, `PRAGMA user_version = 0`, and `Settings.MigrationKey = 5`. The database does not identify an app release, so this gate limits writes to the inspected schema signature but cannot establish compatibility across every WorkingHours release. Each distinct CSV `Job`/`Hourly Rate` pair maps to a rate-specific project. New projects use the destination's default project's currency and receive a new task color; they do not copy its color. CSV tags become ordinary work-unit tags.

The import requires `Job`, `Clocked In`, `Clocked Out`, `Duration`, and `Hourly Rate`. Clock fields use `MM/DD/YY h:mm AM/PM`; duration and rates are decimal numbers. `Comment`, semicolon-separated `Tags`, and `TotalEarningsAdjustment` are imported when present. Positive earnings adjustments become separate lump-sum projects and zero-duration work units; negative earnings adjustments are rejected. `TotalMileage` is ignored by request. Other non-empty columns, including `Breaks` and `TotalTimeAdjustment`, are reported as retained only in the unchanged source CSV.

CSV `Clocked In`, `Clocked Out`, and `Duration` map directly to `WorkUnits.Start`, `End`, and `Duration` after conversion to .NET ticks. Duration is already adjusted; do not reapply `TotalTimeAdjustment`. If `End - Start - Duration` is non-negative, it represents aggregate excluded time, but the individual break intervals are not represented in WorkingHours and remain in the unchanged CSV.

New tag colors are assigned by `whx.services.colors.tag_colors`, and new task colors by `task_colors`; both reuse one palette generator with different initial hue offsets. It uses golden-angle hue spacing with three coordinated saturation/brightness tones, encoded as opaque `#FFRRGGBB`. Task allocation skips existing project colors case-insensitively and colors already generated during the import, including colors assigned to lump-sum bonus projects. Tag allocation retains its existing behavior of skipping existing tag colors and colors already generated in the import. Existing task and tag colors are not changed. Synthetic tests verify 1,000 unique colors per palette, deterministic generation, and collision avoidance; perceptual separation naturally decreases as counts grow.

## Combined migration

`whx db migrate SOURCE BACKUP [DESTINATION]` calls `whx.services.migration.migrate_csv`. It composes extraction, CSV import, and compression services without duplicating mapping or database logic. Intermediate databases live in a temporary directory cleaned up on success or failure. The final backup defaults to `BACKUP-imported.wh` with unique numeric suffixes on collisions. Explicit destinations cannot be either input.

`whx db import SOURCE DATABASE` and `whx db migrate` accept `--file-type`, defaulting to `csv`. CSV is the only supported choice; unsupported formats are rejected by Click before running services.

The returned `MigrationSummary` contains the final backup path and import statistics. Its nested import summary's database path refers to a removed intermediate file; CLI output uses the final backup path instead. Unmapped-field reports and restore warnings remain visible in the combined command.
