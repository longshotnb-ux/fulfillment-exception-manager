"""FastAPI entrypoint for the Fulfillment Exception Manager."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi import Path as APIPath
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .database import (
    CSVValidationError,
    MAX_SQLITE_INTEGER,
    MAX_ORDER_ID,
    connect,
    database_is_empty,
    import_csv_file,
    import_csv_text,
    initialize_database,
)


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_DATABASE = ROOT / "data" / "exceptions.db"
DEFAULT_SEED_CSV = ROOT / "data" / "orders.csv"
ALLOWED_STATUSES = ("Open", "Investigating", "Resolved")
MAX_CSV_BYTES = 1_000_000
OrderId = Annotated[int, APIPath(ge=1, le=MAX_ORDER_ID)]


class ExceptionUpdate(BaseModel):
    status: Optional[Literal["Open", "Investigating", "Resolved"]] = None
    notes: Optional[str] = Field(default=None, max_length=500)


def create_app(
    database_path: Path | str | None = None,
    seed_csv: Path | str | None = None,
    auto_seed: bool = True,
) -> FastAPI:
    resolved_database = Path(
        database_path or os.environ.get("FEM_DATABASE_PATH", DEFAULT_DATABASE)
    )
    resolved_seed_csv = Path(seed_csv or DEFAULT_SEED_CSV)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        initialize_database(application.state.database_path)
        if application.state.auto_seed and database_is_empty(
            application.state.database_path
        ):
            if not application.state.seed_csv.exists():
                raise RuntimeError(
                    f"Seed CSV not found: {application.state.seed_csv}"
                )
            import_csv_file(
                application.state.database_path, application.state.seed_csv
            )
        yield

    application = FastAPI(
        title="Fulfillment Exception Manager",
        version="0.1.0",
        description="A small operations workflow app for triaging late deliveries.",
        lifespan=lifespan,
    )
    application.state.database_path = resolved_database
    application.state.seed_csv = resolved_seed_csv
    application.state.auto_seed = auto_seed
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @application.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path in {"/docs", "/redoc"}:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self' 'unsafe-inline' "
                "https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' "
                "https://cdn.jsdelivr.net; img-src 'self' data: "
                "https://fastapi.tiangolo.com; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'"
            )
        else:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; object-src 'none'; base-uri 'none'; "
                "frame-ancestors 'none'"
            )
        return response

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/summary")
    def summary() -> dict:
        with connect(application.state.database_path) as connection:
            totals = connection.execute(
                """
                SELECT
                    COUNT(*) AS total_orders,
                    SUM(CASE WHEN actual_days > promised_days THEN 1 ELSE 0 END)
                        AS late_orders,
                    SUM(CASE
                        WHEN actual_days > promised_days
                            AND exception_status != 'Resolved' THEN 1 ELSE 0
                    END) AS active_exceptions,
                    SUM(CASE
                        WHEN actual_days > promised_days
                            AND exception_status = 'Resolved' THEN 1 ELSE 0
                    END) AS resolved_exceptions,
                    ROUND(SUM(CASE
                        WHEN actual_days > promised_days
                            AND exception_status != 'Resolved' THEN revenue ELSE 0
                    END), 2) AS revenue_at_risk,
                    ROUND(AVG(CASE
                        WHEN actual_days > promised_days
                            THEN actual_days - promised_days ELSE NULL
                    END), 2) AS average_delay_days
                FROM orders
                """
            ).fetchone()
            workflow_rows = connection.execute(
                """
                SELECT exception_status AS status, COUNT(*) AS count
                FROM orders
                WHERE actual_days > promised_days
                GROUP BY exception_status
                """
            ).fetchall()
            regions = [
                row["region"]
                for row in connection.execute(
                    "SELECT DISTINCT region FROM orders ORDER BY region"
                )
            ]

        total_orders = totals["total_orders"] or 0
        late_orders = totals["late_orders"] or 0
        workflow = {status: 0 for status in ALLOWED_STATUSES}
        workflow.update({row["status"]: row["count"] for row in workflow_rows})
        return {
            "total_orders": total_orders,
            "late_orders": late_orders,
            "late_rate_pct": round(100 * late_orders / total_orders, 1)
            if total_orders
            else 0,
            "active_exceptions": totals["active_exceptions"] or 0,
            "resolved_exceptions": totals["resolved_exceptions"] or 0,
            "revenue_at_risk": totals["revenue_at_risk"] or 0,
            "average_delay_days": totals["average_delay_days"] or 0,
            "workflow": workflow,
            "regions": regions,
        }

    @application.get("/api/exceptions")
    def exceptions(
        status: Optional[str] = Query(default=None),
        region: Optional[str] = Query(default=None, max_length=80),
        q: Optional[str] = Query(default=None, max_length=80),
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0, le=MAX_SQLITE_INTEGER),
    ) -> dict:
        if status and status not in ALLOWED_STATUSES:
            raise HTTPException(status_code=422, detail="Unknown workflow status.")

        conditions = ["actual_days > promised_days"]
        parameters: list[object] = []
        if status:
            conditions.append("exception_status = ?")
            parameters.append(status)
        if region:
            conditions.append("region = ?")
            parameters.append(region)
        if q:
            conditions.append(
                "(CAST(order_id AS TEXT) LIKE ? "
                "OR fulfillment_center LIKE ? OR category LIKE ?)"
            )
            search = f"%{q.strip()}%"
            parameters.extend((search, search, search))

        where_clause = " AND ".join(conditions)
        with connect(application.state.database_path) as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM orders WHERE {where_clause}", parameters
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT
                    order_id, order_date, region, fulfillment_center, category,
                    units, revenue, promised_days, actual_days,
                    actual_days - promised_days AS delay_days,
                    defect, returned, exception_status AS status,
                    exception_notes AS notes, updated_at
                FROM orders
                WHERE {where_clause}
                ORDER BY
                    CASE exception_status
                        WHEN 'Open' THEN 0
                        WHEN 'Investigating' THEN 1
                        ELSE 2
                    END,
                    delay_days DESC,
                    order_date DESC,
                    order_id DESC
                LIMIT ? OFFSET ?
                """,
                [*parameters, limit, offset],
            ).fetchall()

        return {
            "items": [dict(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @application.get("/api/exceptions/{order_id}/history")
    def exception_history(
        order_id: OrderId,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=MAX_SQLITE_INTEGER),
    ) -> dict:
        with connect(application.state.database_path) as connection:
            if not connection.execute(
                "SELECT 1 FROM orders WHERE order_id = ?", (order_id,)
            ).fetchone():
                raise HTTPException(status_code=404, detail="Order not found.")
            total = connection.execute(
                "SELECT COUNT(*) FROM exception_history WHERE order_id = ?",
                (order_id,),
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT id, previous_status, status, previous_notes, notes, created_at "
                "FROM exception_history WHERE order_id = ? "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (order_id, limit, offset),
            ).fetchall()
        return {"items": [dict(row) for row in rows], "total": total}

    @application.patch("/api/exceptions/{order_id}")
    def update_exception(order_id: OrderId, payload: ExceptionUpdate) -> dict:
        if payload.status is None and payload.notes is None:
            raise HTTPException(
                status_code=422, detail="Provide a status, notes, or both."
            )

        with connect(application.state.database_path) as connection:
            # Read and write the before/after history under the same writer lock.
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT order_id, actual_days, promised_days,
                       exception_status, exception_notes
                FROM orders WHERE order_id = ?
                """,
                (order_id,),
            ).fetchone()
            if not existing or existing["actual_days"] <= existing["promised_days"]:
                raise HTTPException(
                    status_code=404,
                    detail="Late-delivery exception not found.",
                )

            status = payload.status or existing["exception_status"]
            notes = payload.notes.strip() if payload.notes is not None else existing["exception_notes"]
            if (status, notes) != (existing["exception_status"], existing["exception_notes"]):
                connection.execute(
                    "UPDATE orders SET exception_status = ?, exception_notes = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE order_id = ?",
                    (status, notes, order_id),
                )
                connection.execute(
                    "INSERT INTO exception_history "
                    "(order_id, previous_status, status, previous_notes, notes) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (order_id, existing["exception_status"], status, existing["exception_notes"], notes),
                )
            updated = connection.execute(
                """
                SELECT order_id, exception_status AS status,
                       exception_notes AS notes, updated_at
                FROM orders WHERE order_id = ?
                """,
                (order_id,),
            ).fetchone()

        return dict(updated)

    @application.post("/api/import")
    async def import_orders(request: Request) -> dict:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                declared_length = int(content_length)
            except ValueError as error:
                raise HTTPException(
                    status_code=400, detail="Invalid Content-Length header."
                ) from error
            if declared_length > MAX_CSV_BYTES:
                raise HTTPException(status_code=413, detail="CSV file is too large.")
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_CSV_BYTES:
                raise HTTPException(status_code=413, detail="CSV file is too large.")
            body.extend(chunk)
        try:
            csv_text = body.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise HTTPException(
                status_code=422, detail="CSV must be UTF-8 encoded."
            ) from error
        try:
            return import_csv_text(application.state.database_path, csv_text)
        except CSVValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    return application


app = create_app()
