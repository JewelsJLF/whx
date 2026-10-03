"""Tests for parsing synthetic HoursTracker CSV data."""

from decimal import Decimal
from pathlib import Path

import pytest

from whx.services.hourstracker_csv import (
    convert_time_fields,
    read_hourstracker_csv,
)

FIXTURE = Path(__file__).parent / "fixtures" / "hourstracker_export.csv"


def test_parser_preserves_headers_and_all_fields() -> None:
    """Optional fields and CSV values remain available without interpretation."""
    parsed = read_hourstracker_csv(FIXTURE)

    assert parsed.headers == (
        "Job",
        "Clocked In",
        "Clocked Out",
        "Duration",
        "Hourly Rate",
        "Earnings",
        "Comment",
        "Tags",
        "Breaks",
        "Adjustments",
        "TotalTimeAdjustment",
        "TotalEarningsAdjustment",
        "TotalMileage",
    )
    assert len(parsed.rows) == 4
    assert parsed.rows[0]["Comment"] == ""
    assert parsed.rows[0]["Tags"] == "Work;FY24"
    assert parsed.rows[1]["Breaks"] == "0.5h (12:00 PM to 12:30 PM)"
    assert parsed.rows[1]["TotalTimeAdjustment"] == "-0.5"
    assert parsed.rows[2]["Breaks"].count(";") == 1
    assert parsed.rows[3]["Adjustments"] == "0.5h (manual adjustment)"
    assert all(isinstance(value, str) for row in parsed.rows for value in row.values())


def test_fixture_preserves_csv_time_fields() -> None:
    parsed = read_hourstracker_csv(FIXTURE)

    for row in parsed.rows:
        times = convert_time_fields(
            row["Clocked In"], row["Clocked Out"], row["Duration"]
        )
        assert times.duration_ticks == int(
            Decimal(row["Duration"]) * Decimal(36_000_000_000)
        )
        assert times.end_ticks > times.start_ticks


@pytest.mark.parametrize(
    ("clock_in", "clock_out", "duration", "message"),
    [
        ("invalid", "01/15/24 4:00 PM", "0", "must use MM/DD/YY"),
        ("01/15/24 8:00 AM", "01/15/24 4:00 PM", "invalid", "decimal number"),
        ("01/15/24 8:00 AM", "01/15/24 4:00 PM", "NaN", "finite"),
        ("01/15/24 8:00 AM", "01/15/24 4:00 PM", "-8", "non-negative"),
        ("01/15/24 8:00 AM", "01/15/24 7:00 AM", "8", "earlier than clock-in"),
        ("01/15/24 8:00 AM", "01/15/24 8:00 AM", "1", "require a zero duration"),
    ],
)
def test_time_conversion_rejects_invalid_values(
    clock_in: str, clock_out: str, duration: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        convert_time_fields(clock_in, clock_out, duration)


def test_time_conversion_preserves_zero_duration_with_equal_timestamps() -> None:
    times = convert_time_fields("01/15/24 8:00 AM", "01/15/24 8:00 AM", "0")

    assert times.start_ticks == times.end_ticks
    assert times.duration_ticks == 0


def test_csv_duration_and_interval_reveal_aggregate_break_time() -> None:
    row = read_hourstracker_csv(FIXTURE).rows[1]
    times = convert_time_fields(row["Clocked In"], row["Clocked Out"], row["Duration"])

    assert times.end_ticks - times.start_ticks - times.duration_ticks == (
        30 * 60 * 10_000_000
    )


def test_parser_preserves_unrecognized_columns_and_multiline_values(
    tmp_path: Path,
) -> None:
    source = tmp_path / "extended.csv"
    source.write_bytes(
        '\ufeffJob,Extra\n"Example Job","first line\nsecond line"\n'.encode("utf-8")
    )

    parsed = read_hourstracker_csv(source)

    assert parsed.headers == ("Job", "Extra")
    assert parsed.rows == ({"Job": "Example Job", "Extra": "first line\nsecond line"},)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("", "CSV file is empty"),
        ("Job,,Duration\njob,tag,8\n", "header names must not be empty"),
        ("Job,Job\nfirst,second\n", "header names must be unique"),
        ("Job,Duration\njob\n", "has 1 fields; expected 2"),
        ("Job,Duration\njob,8,extra\n", "has 3 fields; expected 2"),
        ('Job,Duration\njob,"unterminated\n', "Invalid CSV data"),
    ],
)
def test_parser_rejects_malformed_csv(
    content: str, message: str, tmp_path: Path
) -> None:
    source = tmp_path / "malformed.csv"
    source.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        read_hourstracker_csv(source)
