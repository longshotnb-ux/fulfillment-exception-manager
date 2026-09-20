"""SQLite persistence and CSV validation for fulfillment orders."""

from __future__ import annotations

import csv
import math
import sqlite3
from datetime import date
from io import StringIO
from pathlib import Path
from typing import Any


ORDER_COLUMNS = (
    "order_id",
    "order_date",
    "region",
    "fulfillment_center",
    "category",
    "units",
    "revenue",
    "promised_days",
    "actual_days",
    "defect",
    "returned",
)

MAX_IMPORT_ROWS = 5_000
MAX_SQLITE_INTEGER = 2**63 - 1
# Order IDs travel through JSON and JavaScript Number without string conversion.
MAX_ORDER_ID = 2**53 - 1


class CSVValidationError(ValueError):
    """Raised when an imported CSV cannot be safely loaded."""


def connect(database_path: Path | str) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(database_path: Path | str) -> None:
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    with connect(database_path) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                order_id INTEGER PRIMARY KEY,
                order_date TEXT NOT NULL,
                region TEXT NOT NULL,
                fulfillment_center TEXT NOT NULL,
                category TEXT NOT NULL,
                units INTEGER NOT NULL CHECK (units > 0),
                revenue REAL NOT NULL CHECK (revenue >= 0),
                promised_days INTEGER NOT NULL CHECK (promised_days >= 0),
                actual_days INTEGER NOT NULL CHECK (actual_days >= 0),
                defect INTEGER NOT NULL CHECK (defect IN (0, 1)),
                returned INTEGER NOT NULL CHECK (returned IN (0, 1)),
                exception_status TEXT NOT NULL DEFAULT 'Open'
                    CHECK (exception_status IN ('Open', 'Investigating', 'Resolved')),
                exception_notes TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_orders_exception_queue
            ON orders (exception_status, region, actual_days, promised_days)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS exception_history (
                id INTEGER PRIMARY KEY,
                order_id INTEGER NOT NULL REFERENCES orders(order_id),
                previous_status TEXT NOT NULL,
                status TEXT NOT NULL,
                previous_notes TEXT NOT NULL,
                notes TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_order "
            "ON exception_history (order_id, id DESC)"
        )


def database_is_empty(database_path: Path | str) -> bool:
    with connect(database_path) as connection:
        row = connection.execute("SELECT COUNT(*) AS count FROM orders").fetchone()
    return bool(row and row["count"] == 0)


def import_csv_file(database_path: Path | str, csv_path: Path | str) -> dict[str, int]:
    text = Path(csv_path).read_text(encoding="utf-8-sig")
    return import_csv_text(database_path, text)


def import_csv_text(database_path: Path | str, csv_text: str) -> dict[str, int]:
    try:
        rows = _parse_csv(csv_text)
    except csv.Error as error:
        raise CSVValidationError(f"Invalid CSV: {error}") from error
    order_ids = [row[0] for row in rows]

    with connect(database_path) as connection:
        placeholders = ",".join("?" for _ in order_ids)
        existing_ids: set[int] = set()
        if order_ids:
            existing_ids = {
                row["order_id"]
                for row in connection.execute(
                    f"SELECT order_id FROM orders WHERE order_id IN ({placeholders})",
                    order_ids,
                )
            }

        connection.executemany(
            """
            INSERT INTO orders (
                order_id, order_date, region, fulfillment_center, category,
                units, revenue, promised_days, actual_days, defect, returned
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(order_id) DO UPDATE SET
                order_date = excluded.order_date,
                region = excluded.region,
                fulfillment_center = excluded.fulfillment_center,
                category = excluded.category,
                units = excluded.units,
                revenue = excluded.revenue,
                promised_days = excluded.promised_days,
                actual_days = excluded.actual_days,
                defect = excluded.defect,
                returned = excluded.returned,
                updated_at = CURRENT_TIMESTAMP
            """,
            rows,
        )
        total_revenue = connection.execute("SELECT SUM(revenue) FROM orders").fetchone()[0]
        # Some SQLite versions return NULL when a floating-point sum overflows.
        # Parsed imports always contain rows, so NULL cannot mean an empty table.
        if total_revenue is None or not math.isfinite(total_revenue):
            raise CSVValidationError("Combined order revenue exceeds the supported numeric range.")

    return {
        "imported_rows": len(rows),
        "inserted_rows": len(rows) - len(existing_ids),
        "updated_rows": len(existing_ids),
        "late_orders_in_file": sum(1 for row in rows if row[8] > row[7]),
    }


def _parse_csv(csv_text: str) -> list[tuple[Any, ...]]:
    if not csv_text.strip():
        raise CSVValidationError("The CSV file is empty.")

    reader = csv.DictReader(StringIO(csv_text))
    headers = tuple(reader.fieldnames or ())
    missing = [column for column in ORDER_COLUMNS if column not in headers]
    if missing:
        raise CSVValidationError(
            "Missing required column(s): " + ", ".join(missing) + "."
        )

    parsed_rows: list[tuple[Any, ...]] = []
    seen_order_ids: set[int] = set()

    for line_number, raw_row in enumerate(reader, start=2):
        if len(parsed_rows) >= MAX_IMPORT_ROWS:
            raise CSVValidationError(
                f"CSV imports are limited to {MAX_IMPORT_ROWS:,} rows."
            )
        try:
            row = _validate_row(raw_row, line_number)
        except (TypeError, ValueError) as error:
            if isinstance(error, CSVValidationError):
                raise
            raise CSVValidationError(f"Row {line_number}: {error}") from error

        order_id = row[0]
        if order_id in seen_order_ids:
            raise CSVValidationError(
                f"Row {line_number}: duplicate order_id {order_id} in this file."
            )
        seen_order_ids.add(order_id)
        parsed_rows.append(row)

    if not parsed_rows:
        raise CSVValidationError("The CSV contains headers but no order rows.")
    return parsed_rows


def _validate_row(raw_row: dict[str, str | None], line_number: int) -> tuple[Any, ...]:
    def required_text(column: str) -> str:
        value = (raw_row.get(column) or "").strip()
        if not value:
            raise CSVValidationError(f"Row {line_number}: {column} is required.")
        if len(value) > 80:
            raise CSVValidationError(
                f"Row {line_number}: {column} must be 80 characters or fewer."
            )
        return value

    def whole_number(column: str, minimum: int = 0) -> int:
        value = int(required_text(column))
        if value < minimum:
            raise CSVValidationError(
                f"Row {line_number}: {column} must be at least {minimum}."
            )
        if value > MAX_SQLITE_INTEGER:
            raise CSVValidationError(
                f"Row {line_number}: {column} must be at most {MAX_SQLITE_INTEGER}."
            )
        return value

    order_id = whole_number("order_id", minimum=1)
    if order_id > MAX_ORDER_ID:
        raise CSVValidationError(
            f"Row {line_number}: order_id must be at most {MAX_ORDER_ID} "
            "so it can be represented exactly in the browser."
        )
    order_date = required_text("order_date")
    try:
        date.fromisoformat(order_date)
    except ValueError as error:
        raise CSVValidationError(
            f"Row {line_number}: order_date must use YYYY-MM-DD format."
        ) from error

    region = required_text("region")
    fulfillment_center = required_text("fulfillment_center")
    category = required_text("category")
    units = whole_number("units", minimum=1)

    revenue = float(required_text("revenue"))
    if not math.isfinite(revenue) or revenue < 0:
        raise CSVValidationError(
            f"Row {line_number}: revenue must be a finite, non-negative number."
        )

    promised_days = whole_number("promised_days")
    actual_days = whole_number("actual_days")
    defect = whole_number("defect")
    returned = whole_number("returned")
    if defect not in (0, 1) or returned not in (0, 1):
        raise CSVValidationError(
            f"Row {line_number}: defect and returned must be either 0 or 1."
        )

    return (
        order_id,
        order_date,
        region,
        fulfillment_center,
        category,
        units,
        revenue,
        promised_days,
        actual_days,
        defect,
        returned,
    )
