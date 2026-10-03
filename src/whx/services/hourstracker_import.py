"""Import HoursTracker CSV records into a WorkingHours database."""

import math
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from whx.services.colors import tag_colors, task_colors
from whx.services.database import check_integrity, database_copy
from whx.services.hourstracker_csv import (
    CsvRecords,
    WorkUnitTimes,
    convert_time_fields,
    read_hourstracker_csv,
)

_REQUIRED_HEADERS = {
    "Job",
    "Clocked In",
    "Clocked Out",
    "Duration",
    "Hourly Rate",
}
_IMPORTED_HEADERS = _REQUIRED_HEADERS | {
    "Comment",
    "Tags",
    "TotalEarningsAdjustment",
    "TotalMileage",
}
_REQUIRED_COLUMNS = {
    "Projects": {
        "ProjectId",
        "Name",
        "Icon",
        "Color",
        "HourlyRate",
        "Currency",
        "Details",
        "SortIndex",
        "Hidden",
        "IsLumpSum",
    },
    "Settings": {"SettingId", "Value"},
    "ProjectTags": {"ProjectId", "TagId"},
    "Tags": {
        "TagId",
        "Value",
        "Type",
        "SortIndex",
        "AutoAssign",
        "Details",
        "Icon",
        "Color",
        "AdjustmentValue",
        "Hidden",
    },
    "WorkUnits": {
        "WorkUnitId",
        "ProjectId",
        "Duration",
        "End",
        "Description",
        "Details",
        "Start",
    },
    "WorkUnitTags": {"WorkUnitId", "TagId", "CustomValue"},
}


@dataclass(frozen=True)
class ImportSummary:
    """Counts and source fields associated with one successful import."""

    database_path: Path
    work_units_added: int
    bonus_work_units_added: int
    projects_created: int
    tags_created: int
    mileage_values_ignored: int
    source_only_fields: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class _Entry:
    row_number: int
    job: str
    hourly_rate: Decimal
    times: WorkUnitTimes
    comment: str
    tags: tuple[str, ...]
    earnings_adjustment: Decimal


@dataclass(frozen=True)
class _Project:
    project_id: int
    currency: str


def import_hourstracker_csv(source: Path, database: Path) -> ImportSummary:
    """Import supported HoursTracker fields while preserving the CSV source.

    The input database is preserved; writes go to a unique SQLite backup copy.
    Existing database rows are preserved. Work units use the CSV's clock
    times and already-adjusted duration. Positive earnings adjustments become
    separate zero-duration lump-sum projects and work units. Mileage is ignored
    by design; other unrepresented values remain in the unchanged CSV and are
    included in the returned summary.

    Raises:
        ValueError: If the CSV, database schema, or any mapped value is invalid.
        sqlite3.Error: If SQLite cannot read or update the database.
    """
    if source.resolve() == database.resolve():
        raise ValueError("CSV source and database destination must be different files.")
    records = read_hourstracker_csv(source)
    entries = _parse_entries(records)
    source_only_fields = _source_only_field_counts(records)
    mileage_values_ignored = sum(
        bool((row.get("TotalMileage", "") or "").strip()) for row in records.rows
    )

    with database_copy(database) as (output_path, connection):
        check_integrity(connection)
        _validate_schema(connection)
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            default_currency = _default_project_currency(connection)
            project_names = _project_names_by_job_and_rate(entries, connection)
            bonus_project_names = _bonus_project_names_by_job_rate_and_amount(
                entries, project_names, connection
            )
            next_project_sort = _next_sort_index(connection, "Projects")
            next_tag_sort = _next_sort_index(connection, "Tags")
            tag_cache: dict[str, int] = {}
            project_colors = task_colors(
                {
                    str(row[0])
                    for row in connection.execute("SELECT Color FROM Projects")
                }
            )
            colors = tag_colors(
                {str(row[0]) for row in connection.execute("SELECT Color FROM Tags")}
            )
            project_cache: dict[tuple[str, Decimal], _Project] = {}
            projects_created = 0
            tags_created = 0
            work_units_added = 0
            bonus_work_units_added = 0
            seen_work_units: set[tuple[int, int, int, int, str]] = set()

            for entry in entries:
                project_key = (entry.job, entry.hourly_rate)
                project = project_cache.get(project_key)
                if project is None:
                    project_name = project_names[project_key]
                    project, created = _get_or_create_project(
                        connection,
                        project_name,
                        entry.hourly_rate,
                        default_currency,
                        project_colors,
                        is_lump_sum=False,
                        sort_index=next_project_sort,
                    )
                    projects_created += created
                    next_project_sort += created
                    project_cache[project_key] = project

                work_unit_key = (
                    project.project_id,
                    entry.times.start_ticks,
                    entry.times.end_ticks,
                    entry.times.duration_ticks,
                    entry.comment,
                )
                if work_unit_key in seen_work_units or _work_unit_exists(
                    connection, work_unit_key
                ):
                    raise ValueError(
                        f"CSV record {entry.row_number} matches an existing or "
                        "duplicate work unit; no import was committed."
                    )
                seen_work_units.add(work_unit_key)

                tag_ids, created_tags = _get_or_create_tags(
                    connection,
                    entry.tags,
                    colors,
                    next_tag_sort,
                    tag_cache,
                )
                tags_created += created_tags
                next_tag_sort += created_tags
                work_unit_id = _insert_work_unit(
                    connection,
                    project.project_id,
                    entry.times.start_ticks,
                    entry.times.end_ticks,
                    entry.times.duration_ticks,
                    entry.comment,
                )
                _attach_tags(connection, work_unit_id, tag_ids)
                work_units_added += 1

                if entry.earnings_adjustment:
                    bonus_amount = entry.earnings_adjustment
                    bonus_name = bonus_project_names[
                        (entry.job, entry.hourly_rate, bonus_amount)
                    ]
                    bonus_project, created = _get_or_create_project(
                        connection,
                        bonus_name,
                        bonus_amount,
                        project.currency,
                        project_colors,
                        is_lump_sum=True,
                        sort_index=next_project_sort,
                    )
                    projects_created += created
                    next_project_sort += created
                    bonus_key = (
                        bonus_project.project_id,
                        entry.times.end_ticks,
                        entry.times.end_ticks,
                        0,
                        f"Earnings adjustment from CSV record {entry.row_number}",
                    )
                    if _work_unit_exists(connection, bonus_key):
                        raise ValueError(
                            f"CSV record {entry.row_number} has already been "
                            "imported as a bonus; no import was committed."
                        )
                    bonus_work_unit_id = _insert_work_unit(
                        connection,
                        bonus_project.project_id,
                        entry.times.end_ticks,
                        entry.times.end_ticks,
                        0,
                        bonus_key[4],
                    )
                    _attach_tags(connection, bonus_work_unit_id, tag_ids)
                    bonus_work_units_added += 1

            check_integrity(connection)

        check_integrity(connection)

    return ImportSummary(
        database_path=output_path,
        work_units_added=work_units_added,
        bonus_work_units_added=bonus_work_units_added,
        projects_created=projects_created,
        tags_created=tags_created,
        mileage_values_ignored=mileage_values_ignored,
        source_only_fields=source_only_fields,
    )


def _parse_entries(records: CsvRecords) -> tuple[_Entry, ...]:
    missing = _REQUIRED_HEADERS - set(records.headers)
    if missing:
        raise ValueError(
            "CSV is missing required columns: " + ", ".join(sorted(missing))
        )
    if not records.rows:
        raise ValueError("CSV contains no data records.")

    entries: list[_Entry] = []
    for row_number, row in enumerate(records.rows, start=2):
        job = row["Job"].strip()
        if not job:
            raise ValueError(f"CSV record {row_number} has an empty Job.")
        rate = _decimal_value(row["Hourly Rate"], "Hourly Rate", row_number)
        if rate < 0:
            raise ValueError(f"CSV record {row_number} has a negative Hourly Rate.")
        try:
            times = convert_time_fields(
                row["Clocked In"], row["Clocked Out"], row["Duration"]
            )
        except ValueError as error:
            raise ValueError(f"CSV record {row_number}: {error}") from error
        earnings_adjustment = _decimal_value(
            row.get("TotalEarningsAdjustment", ""),
            "TotalEarningsAdjustment",
            row_number,
            empty_is_zero=True,
        )
        if earnings_adjustment < 0:
            raise ValueError(
                f"CSV record {row_number} has a negative earnings adjustment."
            )
        tags = _parse_tags(row.get("Tags", ""), row_number)
        entries.append(
            _Entry(
                row_number=row_number,
                job=job,
                hourly_rate=rate,
                times=times,
                comment=row.get("Comment", ""),
                tags=tags,
                earnings_adjustment=earnings_adjustment,
            )
        )
    return tuple(entries)


def _decimal_value(
    value: str, field: str, row_number: int, *, empty_is_zero: bool = False
) -> Decimal:
    if empty_is_zero and not value.strip():
        return Decimal(0)
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(
            f"CSV record {row_number} has an invalid {field} value."
        ) from error
    if not number.is_finite():
        raise ValueError(f"CSV record {row_number} has a non-finite {field}.")
    return number


def _parse_tags(value: str, row_number: int) -> tuple[str, ...]:
    if not value.strip():
        return ()
    tags = tuple(tag.strip() for tag in value.split(";"))
    if any(not tag for tag in tags):
        raise ValueError(f"CSV record {row_number} has an empty tag.")
    if len(set(tags)) != len(tags):
        raise ValueError(f"CSV record {row_number} repeats a tag.")
    return tags


def _source_only_field_counts(
    records: CsvRecords,
) -> tuple[tuple[str, int], ...]:
    counts = []
    for header in records.headers:
        if header in _IMPORTED_HEADERS:
            continue
        populated = sum(
            bool((row.get(header, "") or "").strip()) for row in records.rows
        )
        if populated:
            counts.append((header, populated))
    return tuple(counts)


def _validate_schema(connection: sqlite3.Connection) -> None:
    user_version = connection.execute("PRAGMA user_version").fetchone()[0]
    if user_version != 0:
        raise ValueError(
            f"Unsupported WorkingHours schema: expected user_version 0, got "
            f"{user_version}."
        )
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    for table, required_columns in _REQUIRED_COLUMNS.items():
        if table not in tables:
            raise ValueError(f"Database is missing required table {table}.")
        columns = {
            row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')
        }
        missing = required_columns - columns
        if missing:
            raise ValueError(
                f"Table {table} is missing required columns: "
                + ", ".join(sorted(missing))
            )
    migration_key = connection.execute(
        "SELECT Value FROM Settings WHERE SettingId = 'MigrationKey'"
    ).fetchone()
    if migration_key is None or migration_key[0] != "5":
        raise ValueError(
            "Unsupported WorkingHours schema: expected MigrationKey 5 "
            "from the 2.17.9.0 sample."
        )


def _default_project_currency(connection: sqlite3.Connection) -> str:
    setting = connection.execute(
        "SELECT Value FROM Settings WHERE SettingId = 'DefaultProjectId'"
    ).fetchone()
    if setting is None or setting[0] is None:
        raise ValueError("Database has no usable DefaultProjectId setting.")
    try:
        default_project_id = int(setting[0])
    except (TypeError, ValueError) as error:
        raise ValueError("Database DefaultProjectId is invalid.") from error
    project = connection.execute(
        "SELECT Currency FROM Projects WHERE ProjectId = ?",
        (default_project_id,),
    ).fetchone()
    if project is None or not project[0]:
        raise ValueError("Default project must have a currency.")
    return str(project[0])


def _next_sort_index(connection: sqlite3.Connection, table: str) -> int:
    if table == "Projects":
        query = "SELECT MAX(SortIndex) FROM Projects"
    elif table == "Tags":
        query = "SELECT MAX(SortIndex) FROM Tags"
    else:
        raise ValueError(f"Unsupported sort-index table: {table}")
    value = connection.execute(query).fetchone()[0]
    return int(value or 0) + 1


def _project_names_by_job_and_rate(
    entries: tuple[_Entry, ...],
    connection: sqlite3.Connection,
) -> dict[tuple[str, Decimal], str]:
    first_occurrences: dict[tuple[str, Decimal], tuple[int, int]] = {}
    for entry in entries:
        key = (entry.job, entry.hourly_rate)
        occurrence = (entry.times.start_ticks, entry.row_number)
        if key not in first_occurrences or occurrence < first_occurrences[key]:
            first_occurrences[key] = occurrence

    projects = connection.execute(
        "SELECT Name, HourlyRate, IsLumpSum FROM Projects"
    ).fetchall()
    project_names: dict[tuple[str, Decimal], str] = {}
    used_suffixes = set()
    for name, _, _ in projects:
        existing_suffix = _alphabetic_suffix_index(str(name).rsplit(" ", 1)[-1])
        if existing_suffix is not None:
            used_suffixes.add(existing_suffix)
    for job, rate in first_occurrences:
        candidates = [
            str(name)
            for name, stored_rate, is_lump_sum in projects
            if not is_lump_sum
            and Decimal(str(stored_rate)) == rate
            and str(name).startswith(f"{job} ")
            and _alphabetic_suffix_index(str(name)[len(job) + 1 :]) is not None
        ]
        if len(candidates) > 1:
            raise ValueError(
                f"Database has multiple projects matching job {job!r} at rate "
                f"{rate}; no import was committed."
            )
        if candidates:
            project_names[(job, rate)] = candidates[0]

    next_suffix = 0
    keys = sorted(first_occurrences, key=first_occurrences.__getitem__)
    existing_names = {str(name) for name, _, _ in projects}
    for job, rate in keys:
        if (job, rate) in project_names:
            continue
        while (
            next_suffix in used_suffixes
            or f"{job} {_alphabetic_suffix(next_suffix)}" in existing_names
        ):
            next_suffix += 1
        new_suffix = _alphabetic_suffix(next_suffix)
        project_names[(job, rate)] = f"{job} {new_suffix}"
        used_suffixes.add(next_suffix)
        next_suffix += 1
    return project_names


def _alphabetic_suffix_index(suffix: str) -> int | None:
    if not suffix or any(character < "A" or character > "Z" for character in suffix):
        return None
    number = 0
    for character in suffix:
        number = number * 26 + ord(character) - ord("A") + 1
    return number - 1


def _alphabetic_suffix(index: int) -> str:
    suffix = ""
    number = index + 1
    while number:
        number, remainder = divmod(number - 1, 26)
        suffix = chr(ord("A") + remainder) + suffix
    return suffix


def _bonus_project_names_by_job_rate_and_amount(
    entries: tuple[_Entry, ...],
    project_names: dict[tuple[str, Decimal], str],
    connection: sqlite3.Connection,
) -> dict[tuple[str, Decimal, Decimal], str]:
    first_occurrences: dict[tuple[str, Decimal, Decimal], tuple[int, int]] = {}
    for entry in entries:
        if not entry.earnings_adjustment:
            continue
        key = (entry.job, entry.hourly_rate, entry.earnings_adjustment)
        occurrence = (entry.times.start_ticks, entry.row_number)
        if key not in first_occurrences or occurrence < first_occurrences[key]:
            first_occurrences[key] = occurrence

    keys_by_project: dict[str, list[tuple[str, Decimal, Decimal]]] = {}
    for job, rate, amount in first_occurrences:
        keys_by_project.setdefault(project_names[(job, rate)], []).append(
            (job, rate, amount)
        )

    projects = connection.execute(
        "SELECT Name, HourlyRate, IsLumpSum FROM Projects"
    ).fetchall()
    bonus_names: dict[tuple[str, Decimal, Decimal], str] = {}
    for project_name, keys in keys_by_project.items():
        keys.sort(key=first_occurrences.__getitem__)
        used_suffixes = set()
        for name, _, _ in projects:
            existing_suffix = _numeric_suffix(str(name), f"{project_name}.")
            if existing_suffix is not None:
                used_suffixes.add(existing_suffix)
        existing_names = {str(name) for name, _, _ in projects}
        for job, rate, amount in keys:
            candidates = [
                str(name)
                for name, stored_rate, is_lump_sum in projects
                if is_lump_sum
                and Decimal(str(stored_rate)) == amount
                and _numeric_suffix(str(name), f"{project_name}.") is not None
            ]
            if len(candidates) > 1:
                raise ValueError(
                    f"Database has multiple bonus projects matching {project_name!r} "
                    f"at amount {amount}; no import was committed."
                )
            if candidates:
                bonus_names[(job, rate, amount)] = candidates[0]

        next_suffix = 1
        for job, rate, amount in keys:
            key = (job, rate, amount)
            if key in bonus_names:
                continue
            while (
                next_suffix in used_suffixes
                or f"{project_name}.{next_suffix}" in existing_names
            ):
                next_suffix += 1
            bonus_names[key] = f"{project_name}.{next_suffix}"
            used_suffixes.add(next_suffix)
            next_suffix += 1
    return bonus_names


def _numeric_suffix(name: str, prefix: str) -> int | None:
    if not name.startswith(prefix):
        return None
    suffix = name[len(prefix) :]
    if not suffix.isdecimal() or int(suffix) < 1:
        return None
    return int(suffix)


def _get_or_create_project(
    connection: sqlite3.Connection,
    name: str,
    rate: Decimal,
    currency: str,
    colors: Iterator[str],
    *,
    is_lump_sum: bool,
    sort_index: int,
) -> tuple[_Project, int]:
    existing = connection.execute(
        "SELECT ProjectId, HourlyRate, Currency, IsLumpSum "
        "FROM Projects WHERE Name = ?",
        (name,),
    ).fetchall()
    if len(existing) > 1:
        raise ValueError(f"Database has multiple projects named {name!r}.")
    if existing:
        project_id, stored_rate, stored_currency, stored_lump_sum = existing[0]
        if (
            Decimal(str(stored_rate)) != rate
            or stored_currency != currency
            or bool(stored_lump_sum) != is_lump_sum
        ):
            raise ValueError(
                f"Existing project {name!r} conflicts with the import rate or "
                "currency; no import was committed."
            )
        return _Project(int(project_id), str(stored_currency)), 0

    sqlite_rate = float(rate)
    if not math.isfinite(sqlite_rate):
        raise ValueError(f"Project rate for {name!r} is outside SQLite range.")
    color = next(colors)
    cursor = connection.execute(
        "INSERT INTO Projects "
        "(Name, Icon, Color, HourlyRate, Currency, Details, SortIndex, Hidden, "
        "IsLumpSum) "
        "VALUES (?, NULL, ?, ?, ?, '', ?, 0, ?)",
        (name, color, sqlite_rate, currency, sort_index, int(is_lump_sum)),
    )
    if cursor.lastrowid is None:
        raise sqlite3.DatabaseError(f"Failed to create project {name!r}.")
    return _Project(cursor.lastrowid, currency), 1


def _get_or_create_tags(
    connection: sqlite3.Connection,
    names: tuple[str, ...],
    colors: Iterator[str],
    next_sort_index: int,
    cache: dict[str, int],
) -> tuple[tuple[int, ...], int]:
    tag_ids = []
    created = 0
    for name in names:
        tag_id = cache.get(name)
        if tag_id is None:
            existing = connection.execute(
                "SELECT TagId FROM Tags WHERE Value = ? AND Type = 0",
                (name,),
            ).fetchall()
            if len(existing) > 1:
                raise ValueError(f"Database has multiple ordinary tags named {name!r}.")
            if existing:
                tag_id = int(existing[0][0])
            else:
                cursor = connection.execute(
                    "INSERT INTO Tags "
                    "(Value, Type, SortIndex, AutoAssign, Details, Icon, Color, "
                    "AdjustmentValue, Hidden) "
                    "VALUES (?, 0, ?, 0, '', NULL, ?, 0, 0)",
                    (name, next_sort_index + created, next(colors)),
                )
                if cursor.lastrowid is None:
                    raise sqlite3.DatabaseError(f"Failed to create tag {name!r}.")
                tag_id = cursor.lastrowid
                created += 1
            cache[name] = tag_id
        tag_ids.append(tag_id)
    return tuple(tag_ids), created


def _work_unit_exists(
    connection: sqlite3.Connection,
    key: tuple[int, int, int, int, str],
) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM WorkUnits "
            "WHERE ProjectId = ? AND Start = ? AND End = ? "
            "AND Duration = ? AND Description = ? LIMIT 1",
            key,
        ).fetchone()
        is not None
    )


def _insert_work_unit(
    connection: sqlite3.Connection,
    project_id: int,
    start_ticks: int,
    end_ticks: int,
    duration_ticks: int,
    description: str,
) -> int:
    cursor = connection.execute(
        "INSERT INTO WorkUnits "
        "(ProjectId, Duration, End, Description, Details, Start) "
        "VALUES (?, ?, ?, ?, '', ?)",
        (project_id, duration_ticks, end_ticks, description, start_ticks),
    )
    if cursor.lastrowid is None:
        raise sqlite3.DatabaseError("Failed to create a work unit.")
    return cursor.lastrowid


def _attach_tags(
    connection: sqlite3.Connection, work_unit_id: int, tag_ids: tuple[int, ...]
) -> None:
    connection.executemany(
        "INSERT INTO WorkUnitTags (WorkUnitId, TagId, CustomValue) VALUES (?, ?, NULL)",
        ((work_unit_id, tag_id) for tag_id in tag_ids),
    )
