"""Tests for importing synthetic HoursTracker data into SQLite."""

import csv
import gzip
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from click.testing import CliRunner

from whx.cli import cli
from whx.services.colors import tag_colors, task_colors
from whx.services.hourstracker_import import import_hourstracker_csv
from whx.services.migration import migrate_csv

FIXTURE = Path(__file__).parent / "fixtures" / "hourstracker_export.csv"


def _create_database(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            """
            CREATE TABLE Projects (
                ProjectId INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                Name varchar NOT NULL,
                Icon varchar,
                Color varchar NOT NULL,
                HourlyRate float NOT NULL,
                Currency varchar NOT NULL,
                Details varchar NOT NULL,
                SortIndex integer NOT NULL,
                Hidden integer NOT NULL,
                IsLumpSum integer NOT NULL
            );
            CREATE TABLE Settings (
                SettingId varchar PRIMARY KEY NOT NULL,
                Value varchar
            );
            CREATE TABLE ProjectTags (
                ProjectId integer NOT NULL,
                TagId integer NOT NULL
            );
            CREATE TABLE Tags (
                TagId INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                Value varchar NOT NULL,
                Type integer NOT NULL,
                SortIndex integer NOT NULL,
                AutoAssign integer NOT NULL,
                Details varchar NOT NULL,
                Icon varchar,
                Color varchar NOT NULL,
                AdjustmentValue float NOT NULL,
                Hidden integer NOT NULL
            );
            CREATE TABLE WorkUnits (
                WorkUnitId INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                ProjectId integer NOT NULL,
                Duration bigint NOT NULL,
                End bigint NOT NULL,
                Description varchar NOT NULL,
                Details varchar NOT NULL,
                Start bigint NOT NULL
            );
            CREATE TABLE WorkUnitTags (
                WorkUnitId integer NOT NULL,
                TagId integer NOT NULL,
                CustomValue varchar
            );
            INSERT INTO Projects
                (ProjectId, Name, Color, HourlyRate, Currency, Details,
                 SortIndex, Hidden, IsLumpSum)
            VALUES (1, 'Default', '#FF607D8B', 0, '$', '', 0, 0, 0);
            INSERT INTO Settings (SettingId, Value)
            VALUES ('DefaultProjectId', '1');
            INSERT INTO Settings (SettingId, Value)
            VALUES ('MigrationKey', '5');
            """
        )


def test_import_preserves_existing_database_and_creates_lump_sum_bonuses(
    tmp_path: Path,
) -> None:
    database = tmp_path / "WorkingHours.db"
    _create_database(database)
    original_database = database.read_bytes()
    original_csv = FIXTURE.read_bytes()

    result = import_hourstracker_csv(FIXTURE, database)

    assert result.work_units_added == 4
    assert result.bonus_work_units_added == 2
    assert result.projects_created == 3
    assert result.tags_created == 3
    assert result.mileage_values_ignored == 3
    assert ("Breaks", 2) in result.source_only_fields
    assert ("Earnings", 4) in result.source_only_fields
    assert FIXTURE.read_bytes() == original_csv
    assert database.read_bytes() == original_database
    assert result.database_path == tmp_path / "WorkingHours-imported.db"

    with sqlite3.connect(result.database_path) as connection:
        projects = connection.execute(
            "SELECT Name, HourlyRate, Currency, IsLumpSum, Color FROM Projects "
            "ORDER BY ProjectId"
        ).fetchall()
        assert projects[0] == ("Default", 0.0, "$", 0, "#FF607D8B")
        assert ("Example Job A", 25.0, "$", 0) == projects[1][:4]
        assert sum(bool(project[3]) for project in projects) == 2
        imported_colors = [project[4] for project in projects[1:]]
        assert len(set(imported_colors)) == len(imported_colors)
        assert "#FF607D8B" not in imported_colors
        colors = connection.execute("SELECT Color FROM Tags").fetchall()
        assert len(set(colors)) == 3

        work_units = connection.execute(
            "SELECT Duration, Start, End, Description FROM WorkUnits "
            "ORDER BY WorkUnitId"
        ).fetchall()
        assert len(work_units) == 6
        assert work_units[0][0] == 8 * 36_000_000_000
        bonus_units = connection.execute(
            "SELECT WorkUnits.Duration, WorkUnits.Start, WorkUnits.End "
            "FROM WorkUnits JOIN Projects USING (ProjectId) "
            "WHERE Projects.IsLumpSum = 1"
        ).fetchall()
        assert len(bonus_units) == 2
        assert all(row[0] == 0 and row[1] == row[2] for row in bonus_units)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_import_is_atomic_and_refuses_duplicate_work_units(tmp_path: Path) -> None:
    database = tmp_path / "WorkingHours.db"
    _create_database(database)

    summary = import_hourstracker_csv(FIXTURE, database)
    with pytest.raises(ValueError, match="matches an existing or duplicate"):
        import_hourstracker_csv(FIXTURE, summary.database_path)

    assert not (tmp_path / "WorkingHours-imported-imported.db").exists()
    with sqlite3.connect(summary.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM WorkUnits").fetchone() == (6,)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


@pytest.mark.parametrize(
    "file_options", [[], ["--file-type", "csv"], ["--file-type", "CSV"]]
)
def test_cli_import_csv_reports_import_summary(
    tmp_path: Path, file_options: list[str]
) -> None:
    database = tmp_path / "WorkingHours.db"
    _create_database(database)

    result = CliRunner().invoke(
        cli, ["db", "import", *file_options, str(FIXTURE), str(database)]
    )

    assert result.exit_code == 0, result.output
    assert "Imported 4 work units and 2 zero-duration bonus work units" in result.output
    assert "Ignored 3 mileage values as requested" in result.output
    assert str(tmp_path / "WorkingHours-imported.db") in result.output


def test_cli_import_preserves_zero_duration_entry_and_tags(tmp_path: Path) -> None:
    source = tmp_path / "zero-duration.csv"
    with source.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(
            ["Job", "Clocked In", "Clocked Out", "Duration", "Hourly Rate", "Tags"]
        )
        writer.writerow(
            ["Example Job", "01/15/24 8:00 AM", "01/15/24 8:00 AM", "0", "25", "Work"]
        )
    original_csv = source.read_bytes()
    database = tmp_path / "WorkingHours.db"
    _create_database(database)

    result = CliRunner().invoke(cli, ["db", "import", str(source), str(database)])

    assert result.exit_code == 0, result.output
    assert "Imported 1 work units" in result.output
    assert source.read_bytes() == original_csv
    with sqlite3.connect(tmp_path / "WorkingHours-imported.db") as connection:
        units = connection.execute(
            "SELECT Start, End, Duration FROM WorkUnits"
        ).fetchall()
        assert len(units) == 1
        assert units[0][0] == units[0][1]
        assert units[0][2] == 0
        assert connection.execute(
            "SELECT Tags.Value FROM WorkUnitTags JOIN Tags USING (TagId)"
        ).fetchall() == [("Work",)]
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]


def test_import_rejects_missing_default_currency_without_writes(tmp_path: Path) -> None:
    database = tmp_path / "WorkingHours.db"
    _create_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM Settings WHERE SettingId = 'DefaultProjectId'")

    with pytest.raises(ValueError, match="DefaultProjectId"):
        import_hourstracker_csv(FIXTURE, database)

    assert not (tmp_path / "WorkingHours-imported.db").exists()
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM Projects").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM WorkUnits").fetchone() == (0,)


def test_import_rejects_an_unrecognized_migration_key(tmp_path: Path) -> None:
    database = tmp_path / "WorkingHours.db"
    _create_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE Settings SET Value = '6' WHERE SettingId = 'MigrationKey'"
        )

    with pytest.raises(ValueError, match="expected MigrationKey 5"):
        import_hourstracker_csv(FIXTURE, database)


def test_import_rejects_negative_earnings_adjustment(tmp_path: Path) -> None:
    source = tmp_path / "negative-adjustment.csv"
    content = FIXTURE.read_text(encoding="utf-8")
    source.write_text(
        content.replace(',"-0.5","5","3"', ',"-0.5","-5","3"'), encoding="utf-8"
    )
    database = tmp_path / "WorkingHours.db"
    _create_database(database)

    with pytest.raises(ValueError, match="negative earnings adjustment"):
        import_hourstracker_csv(source, database)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM WorkUnits").fetchone() == (0,)


def test_import_names_rate_projects_in_chronological_order(tmp_path: Path) -> None:
    source = tmp_path / "rates.csv"
    with source.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["Job", "Clocked In", "Clocked Out", "Duration", "Hourly Rate"])
        writer.writerow(
            ["Example Job", "01/16/24 8:00 AM", "01/16/24 9:00 AM", "1", "30"]
        )
        writer.writerow(
            ["Example Job", "01/15/24 8:00 AM", "01/15/24 9:00 AM", "1", "25"]
        )
    database = tmp_path / "WorkingHours.db"
    _create_database(database)

    summary = import_hourstracker_csv(source, database)

    assert summary.work_units_added == 2
    with sqlite3.connect(summary.database_path) as connection:
        projects = connection.execute(
            "SELECT Name, HourlyRate, Color FROM Projects WHERE ProjectId != 1"
        ).fetchall()
    assert {(name, rate) for name, rate, _ in projects} == {
        ("Example Job A", 25.0),
        ("Example Job B", 30.0),
    }
    colors = [color for _, _, color in projects]
    assert len(set(colors)) == 2


def test_import_task_colors_avoid_existing_projects_case_insensitively(
    tmp_path: Path,
) -> None:
    database = tmp_path / "WorkingHours.db"
    _create_database(database)
    collision = next(task_colors(set()))
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO Projects "
            "(Name, Color, HourlyRate, Currency, Details, SortIndex, Hidden, "
            "IsLumpSum) VALUES ('Existing', ?, 10, '$', '', 1, 0, 0)",
            (collision.lower(),),
        )

    summary = import_hourstracker_csv(FIXTURE, database)

    with sqlite3.connect(summary.database_path) as connection:
        projects = connection.execute("SELECT Name, Color FROM Projects").fetchall()
    colors = [color.upper() for _, color in projects]
    assert len(set(colors)) == len(colors)
    assert projects[1] == ("Existing", collision.lower())
    assert all(
        color != collision.upper() for name, color in projects if name != "Existing"
    )


def test_import_reuses_existing_task_color(tmp_path: Path) -> None:
    database = tmp_path / "WorkingHours.db"
    _create_database(database)
    existing_color = "#FF123456"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO Projects "
            "(Name, Color, HourlyRate, Currency, Details, SortIndex, Hidden, "
            "IsLumpSum) VALUES ('Example Job A', ?, 25, '$', '', 1, 0, 0)",
            (existing_color,),
        )

    summary = import_hourstracker_csv(FIXTURE, database)

    with sqlite3.connect(summary.database_path) as connection:
        projects = connection.execute(
            "SELECT Name, Color FROM Projects ORDER BY ProjectId"
        ).fetchall()
    assert summary.projects_created == 2
    assert projects[1] == ("Example Job A", existing_color)
    colors = [color.upper() for _, color in projects]
    assert len(set(colors)) == len(colors)


def test_import_rolls_back_when_a_bonus_project_conflicts(tmp_path: Path) -> None:
    database = tmp_path / "WorkingHours.db"
    _create_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO Projects "
            "(Name, Color, HourlyRate, Currency, Details, SortIndex, Hidden, "
            "IsLumpSum) "
            "VALUES (?, '#FF607D8B', 999, '$', '', 1, 0, 1)",
            ("Example Job bonus 2 (CSV row 5)",),
        )

    with pytest.raises(ValueError, match="conflicts with the import rate"):
        import_hourstracker_csv(FIXTURE, database)

    assert not (tmp_path / "WorkingHours-imported.db").exists()
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM Projects").fetchone() == (2,)
        assert connection.execute("SELECT COUNT(*) FROM Tags").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM WorkUnits").fetchone() == (0,)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_repeated_imports_create_unique_outputs_without_changing_inputs(
    tmp_path: Path,
) -> None:
    database = tmp_path / "WorkingHours.db"
    _create_database(database)
    original_database = database.read_bytes()
    first = import_hourstracker_csv(FIXTURE, database)
    original_output = first.database_path.read_bytes()

    second = import_hourstracker_csv(FIXTURE, database)

    assert first.database_path == tmp_path / "WorkingHours-imported.db"
    assert second.database_path == tmp_path / "WorkingHours-imported-1.db"
    assert database.read_bytes() == original_database
    assert first.database_path.read_bytes() == original_output
    with sqlite3.connect(second.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM WorkUnits").fetchone() == (6,)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_import_copies_committed_wal_records(tmp_path: Path) -> None:
    database = tmp_path / "WorkingHours.db"
    _create_database(database)
    source_connection = sqlite3.connect(database)
    try:
        source_connection.execute("PRAGMA journal_mode=WAL")
        source_connection.execute(
            "INSERT INTO Settings VALUES ('SyntheticWalSetting', 'preserved')"
        )
        source_connection.commit()
        original_database = database.read_bytes()

        summary = import_hourstracker_csv(FIXTURE, database)

        assert database.read_bytes() == original_database
        with sqlite3.connect(summary.database_path) as connection:
            assert connection.execute(
                "SELECT Value FROM Settings WHERE SettingId = 'SyntheticWalSetting'"
            ).fetchone() == ("preserved",)
    finally:
        source_connection.close()


def test_import_does_not_create_a_missing_input_database(tmp_path: Path) -> None:
    database = tmp_path / "Missing.db"

    with pytest.raises(sqlite3.OperationalError):
        import_hourstracker_csv(FIXTURE, database)

    assert not database.exists()
    assert not (tmp_path / "Missing-imported.db").exists()


def test_import_preserves_existing_tag_colors_and_avoids_collisions(
    tmp_path: Path,
) -> None:
    database = tmp_path / "WorkingHours.db"
    _create_database(database)
    existing_color = next(tag_colors(set()))
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO Tags "
            "(Value, Type, SortIndex, AutoAssign, Details, Color, "
            "AdjustmentValue, Hidden) "
            "VALUES ('Work', 0, 0, 0, '', ?, 0, 0)",
            (existing_color,),
        )

    summary = import_hourstracker_csv(FIXTURE, database)

    with sqlite3.connect(summary.database_path) as connection:
        tags = dict(connection.execute("SELECT Value, Color FROM Tags"))
        assert tags["Work"] == existing_color
        assert len(set(tags.values())) == len(tags)


@pytest.mark.parametrize("file_options", [[], ["--file-type", "csv"]])
def test_cli_migrates_backup_and_csv_without_overwriting(
    tmp_path: Path, file_options: list[str]
) -> None:
    database = tmp_path / "synthetic.db"
    _create_database(database)
    backup = tmp_path / "WorkingHours.wh"
    backup.write_bytes(gzip.compress(database.read_bytes()))
    database.unlink()
    original_backup = backup.read_bytes()
    original_csv = FIXTURE.read_bytes()
    destination = tmp_path / "WorkingHours-imported.wh"
    destination.write_bytes(b"existing backup")

    result = CliRunner().invoke(
        cli, ["db", "migrate", *file_options, str(FIXTURE), str(backup)]
    )

    output = tmp_path / "WorkingHours-imported-1.wh"
    assert result.exit_code == 0, result.output
    assert str(output) in result.output
    assert "Imported 4 work units and 2 zero-duration bonus work units" in result.output
    assert "retained only in the unchanged source file" in result.output
    assert backup.read_bytes() == original_backup
    assert FIXTURE.read_bytes() == original_csv
    assert destination.read_bytes() == b"existing backup"
    assert set(tmp_path.iterdir()) == {backup, destination, output}
    with sqlite3.connect(":memory:") as connection:
        connection.deserialize(gzip.decompress(output.read_bytes()))
        assert connection.execute("SELECT COUNT(*) FROM WorkUnits").fetchone() == (6,)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_migration_failure_leaves_no_output_or_intermediate_files(
    tmp_path: Path,
) -> None:
    database = tmp_path / "synthetic.db"
    _create_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE Settings SET Value = '6' WHERE SettingId = 'MigrationKey'"
        )
    connection.close()
    backup = tmp_path / "WorkingHours.wh"
    backup.write_bytes(gzip.compress(database.read_bytes()))
    database.unlink()
    original = backup.read_bytes()

    with pytest.raises(ValueError, match="MigrationKey"):
        migrate_csv(FIXTURE, backup)

    assert list(tmp_path.iterdir()) == [backup]
    assert backup.read_bytes() == original


def test_migration_accepts_explicit_destination(tmp_path: Path) -> None:
    database = tmp_path / "synthetic.db"
    _create_database(database)
    backup = tmp_path / "WorkingHours.wh"
    backup.write_bytes(gzip.compress(database.read_bytes()))
    output = tmp_path / "custom.wh"

    summary = migrate_csv(FIXTURE, backup, output)

    assert summary.backup_path == output
    assert output.exists()
    assert not summary.imported.database_path.exists()


def test_migration_rejects_an_input_as_destination(tmp_path: Path) -> None:
    backup = tmp_path / "WorkingHours.wh"
    backup.write_bytes(b"preserve backup")

    with pytest.raises(ValueError, match="Destination must be different"):
        migrate_csv(FIXTURE, backup, backup)

    assert backup.read_bytes() == b"preserve backup"


@pytest.mark.parametrize("command", ["import", "migrate"])
def test_cli_rejects_unsupported_file_type(command: str) -> None:
    result = CliRunner().invoke(cli, ["db", command, "--file-type", "json"])

    assert result.exit_code == 2
    assert "Invalid value for '--file-type'" in result.output


@pytest.mark.parametrize("command", ["import", "migrate"])
def test_cli_help_reports_csv_default(command: str) -> None:
    result = CliRunner().invoke(cli, ["db", command, "--help"])

    assert result.exit_code == 0
    assert "--file-type [csv]" in result.output
    assert "[default: csv]" in result.output
