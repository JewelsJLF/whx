"""Lossless parsing of HoursTracker CSV records."""

import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

_DATETIME_FORMAT = "%m/%d/%y %I:%M %p"
_TICKS_PER_DAY = 864_000_000_000
_TICKS_PER_SECOND = 10_000_000
_TICKS_PER_HOUR = 36_000_000_000


@dataclass(frozen=True)
class CsvRecords:
    """CSV headers and rows, retaining every field as its original string."""

    headers: tuple[str, ...]
    rows: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class WorkUnitTimes:
    """WorkingHours timestamp and duration values, represented as .NET ticks."""

    start_ticks: int
    end_ticks: int
    duration_ticks: int


def read_hourstracker_csv(source: Path) -> CsvRecords:
    """Read CSV records without interpreting or discarding any fields.

    The parser preserves headers and values as strings. Mapping records into
    WorkingHours is intentionally a separate step because database schemas
    differ between app versions.

    Raises:
        ValueError: If the CSV has no usable header or a row has the wrong
            number of fields.
    """
    try:
        with source.open(encoding="utf-8-sig", newline="") as csv_file:
            reader = csv.reader(csv_file, strict=True)
            try:
                headers = next(reader)
            except StopIteration as error:
                raise ValueError(
                    "CSV file is empty; a header row is required."
                ) from error

            if not headers or any(not header.strip() for header in headers):
                raise ValueError("CSV header names must not be empty.")
            if len(set(headers)) != len(headers):
                raise ValueError("CSV header names must be unique.")

            rows: list[dict[str, str]] = []
            for row in reader:
                if len(row) != len(headers):
                    raise ValueError(
                        f"CSV row ending at line {reader.line_num} has "
                        f"{len(row)} fields; expected {len(headers)}."
                    )
                rows.append(dict(zip(headers, row, strict=True)))
    except csv.Error as error:
        raise ValueError(f"Invalid CSV data: {error}") from error
    except UnicodeDecodeError as error:
        raise ValueError("CSV must be encoded as UTF-8.") from error

    return CsvRecords(tuple(headers), tuple(rows))


def convert_time_fields(clock_in: str, clock_out: str, duration: str) -> WorkUnitTimes:
    """Convert CSV clock times and duration to WorkingHours .NET ticks.

    Raises:
        ValueError: If a date or duration is invalid.
    """
    try:
        start = datetime.strptime(clock_in, _DATETIME_FORMAT)
        end = datetime.strptime(clock_out, _DATETIME_FORMAT)
    except ValueError as error:
        raise ValueError(
            "Clock-in and clock-out must use MM/DD/YY h:mm AM/PM."
        ) from error

    if end < start:
        raise ValueError("Clock-out must not be earlier than clock-in.")

    try:
        duration_hours = Decimal(duration)
    except InvalidOperation as error:
        raise ValueError("Duration must be a decimal number of hours.") from error
    if not duration_hours.is_finite() or duration_hours < 0:
        raise ValueError("Duration must be a finite, non-negative number of hours.")
    if end == start and duration_hours != 0:
        raise ValueError("Equal clock-in and clock-out require a zero duration.")

    duration_ticks = duration_hours * _TICKS_PER_HOUR
    if duration_ticks != duration_ticks.to_integral_value():
        raise ValueError("Duration has a precision finer than one tick.")

    return WorkUnitTimes(
        start_ticks=_datetime_to_ticks(start),
        end_ticks=_datetime_to_ticks(end),
        duration_ticks=int(duration_ticks),
    )


def _datetime_to_ticks(value: datetime) -> int:
    delta = value - datetime(1, 1, 1)
    return (
        delta.days * _TICKS_PER_DAY
        + delta.seconds * _TICKS_PER_SECOND
        + delta.microseconds * 10
    )
