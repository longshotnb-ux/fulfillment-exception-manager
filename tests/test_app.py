from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.database import ORDER_COLUMNS


ROOT = Path(__file__).resolve().parents[1]
SEED_CSV = ROOT / "data" / "orders.csv"


@pytest.fixture
def client(tmp_path):
    application = create_app(
        database_path=tmp_path / "test.db",
        seed_csv=SEED_CSV,
    )
    with TestClient(application) as test_client:
        yield test_client


def test_seeded_summary_matches_demo_data(client):
    response = client.get("/api/summary")

    assert response.status_code == 200
    summary = response.json()
    assert summary["total_orders"] == 40
    assert summary["late_orders"] == 17
    assert summary["late_rate_pct"] == 42.5
    assert summary["active_exceptions"] == 17
    assert summary["revenue_at_risk"] == 3564.65
    assert summary["average_delay_days"] == 1.53
    assert summary["workflow"] == {
        "Open": 17,
        "Investigating": 0,
        "Resolved": 0,
    }


def test_exception_queue_filters_by_status_region_and_search(client):
    all_exceptions = client.get("/api/exceptions").json()
    midwest = client.get("/api/exceptions", params={"region": "Midwest"}).json()
    search = client.get("/api/exceptions", params={"q": "FC-LAX"}).json()
    resolved = client.get("/api/exceptions", params={"status": "Resolved"}).json()

    assert all_exceptions["total"] == 17
    assert midwest["total"] > 0
    assert all(item["region"] == "Midwest" for item in midwest["items"])
    assert search["total"] > 0
    assert all(item["fulfillment_center"] == "FC-LAX" for item in search["items"])
    assert resolved["total"] == 0


def test_update_exception_status_and_notes(client):
    response = client.patch(
        "/api/exceptions/1005",
        json={"status": "Investigating", "notes": "Contacted FC-ORD carrier."},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "Investigating"
    assert response.json()["notes"] == "Contacted FC-ORD carrier."

    summary = client.get("/api/summary").json()
    assert summary["workflow"]["Open"] == 16
    assert summary["workflow"]["Investigating"] == 1


def test_cannot_manage_an_on_time_order(client):
    response = client.patch(
        "/api/exceptions/1001",
        json={"status": "Resolved", "notes": "Not actually late."},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Late-delivery exception not found."


def test_csv_import_adds_order_and_preserves_workflow_on_update(client):
    client.patch(
        "/api/exceptions/1002",
        json={"status": "Investigating", "notes": "Existing triage note."},
    )
    csv_text = """order_id,order_date,region,fulfillment_center,category,units,revenue,promised_days,actual_days,defect,returned
1002,2026-01-04,West,FC-LAX,Home,3,99.97,2,4,0,0
2001,2026-05-01,West,FC-PHX,Electronics,1,199.99,2,5,0,0
"""
    response = client.post(
        "/api/import", content=csv_text, headers={"Content-Type": "text/csv"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "imported_rows": 2,
        "inserted_rows": 1,
        "updated_rows": 1,
        "late_orders_in_file": 2,
    }
    investigating = client.get(
        "/api/exceptions", params={"status": "Investigating"}
    ).json()
    assert investigating["items"][0]["order_id"] == 1002
    assert investigating["items"][0]["notes"] == "Existing triage note."


def test_csv_import_rejects_missing_schema(client):
    response = client.post(
        "/api/import",
        content="order_id,order_date\n1,2026-05-01\n",
        headers={"Content-Type": "text/csv"},
    )

    assert response.status_code == 422
    assert "Missing required column" in response.json()["detail"]


@pytest.mark.parametrize("revenue", ["NaN", "inf", "-inf", "-1"])
def test_csv_import_rejects_invalid_revenue(client, revenue):
    csv_text = f"""order_id,order_date,region,fulfillment_center,category,units,revenue,promised_days,actual_days,defect,returned
2001,2026-05-01,West,FC-PHX,Electronics,1,{revenue},2,5,0,0
"""
    response = client.post(
        "/api/import", content=csv_text, headers={"Content-Type": "text/csv"}
    )

    assert response.status_code == 422
    assert "finite, non-negative" in response.json()["detail"]


def test_home_page_and_security_headers(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "Fulfillment Exception Manager" in response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "unsafe-inline" not in response.headers["content-security-policy"]


def test_api_docs_allow_their_required_cdn_assets(client):
    response = client.get("/docs")

    assert response.status_code == 200
    assert "Swagger UI" in response.text
    policy = response.headers["content-security-policy"]
    assert "https://cdn.jsdelivr.net" in policy
    assert "'unsafe-inline'" in policy


def csv_order(**overrides):
    row = dict(zip(ORDER_COLUMNS, [2001, "2026-05-01", "West", "FC-PHX", "Home", 1, 10, 2, 5, 0, 0]))
    row.update(overrides)
    return ",".join(str(row[column]) for column in ORDER_COLUMNS)


def test_all_imported_exceptions_are_reachable_with_correct_totals(tmp_path):
    app = create_app(database_path=tmp_path / "pages.db", auto_seed=False)
    with TestClient(app) as client:
        text = ",".join(ORDER_COLUMNS) + "\n" + "\n".join(
            csv_order(order_id=order_id, region="West" if order_id % 2 else "East")
            for order_id in range(1, 502)
        )
        assert client.post("/api/import", content=text).status_code == 200
        first = client.get("/api/exceptions?limit=500").json()
        second = client.get("/api/exceptions?limit=500&offset=500").json()
        assert first["total"] == second["total"] == 501
        assert len(first["items"]) == 500
        assert len(second["items"]) == 1
        ids = [item["order_id"] for item in first["items"] + second["items"]]
        assert len(set(ids)) == 501
        filtered = client.get("/api/exceptions?region=West&limit=25&offset=25").json()
        assert filtered["total"] == 251
        assert len(filtered["items"]) == 25
        assert all(item["region"] == "West" for item in filtered["items"])
        beyond = client.get("/api/exceptions?offset=501").json()
        assert beyond["items"] == [] and beyond["total"] == 501


@pytest.mark.parametrize("query", ["limit=0", "limit=501", "offset=-1", "offset=99999999999999999999"])
def test_invalid_pagination_is_rejected(client, query):
    assert client.get(f"/api/exceptions?{query}").status_code == 422


@pytest.mark.parametrize("column", ["order_id", "units", "promised_days", "actual_days"])
def test_oversized_csv_integers_are_rejected_atomically(client, column):
    text = ",".join(ORDER_COLUMNS) + "\n" + csv_order(order_id=2002) + "\n" + csv_order(**{column: 2**63})
    response = client.post("/api/import", content=text)
    assert response.status_code == 422
    assert column in response.json()["detail"]
    assert "Row 3" in response.json()["detail"]
    assert client.get("/api/summary").json()["total_orders"] == 40


def test_invalid_path_ids_return_validation_error(client):
    for order_id in (0, 2**53, 2**63):
        assert client.patch(f"/api/exceptions/{order_id}", json={"status": "Resolved"}).status_code == 422
        assert client.get(f"/api/exceptions/{order_id}/history").status_code == 422


def test_order_ids_are_exact_in_browser_and_unsafe_ids_are_rejected(client):
    header = ",".join(ORDER_COLUMNS) + "\n"
    safe = 2**53 - 1
    assert client.post("/api/import", content=header + csv_order(order_id=safe)).status_code == 200
    response = client.patch(f"/api/exceptions/{safe}", json={"status": "Investigating"})
    assert response.status_code == 200 and response.json()["order_id"] == safe
    assert client.get(f"/api/exceptions?q={safe}").json()["items"][0]["order_id"] == safe
    response = client.post("/api/import", content=header + csv_order(order_id=2**53))
    assert response.status_code == 422
    assert "represented exactly" in response.json()["detail"]


def test_combined_revenue_overflow_rolls_back_the_import(client):
    text = ",".join(ORDER_COLUMNS) + "\n" + "\n".join(
        csv_order(order_id=order_id, revenue="1e308") for order_id in (2001, 2002)
    )
    response = client.post("/api/import", content=text)
    assert response.status_code == 422
    assert "Combined order revenue" in response.json()["detail"]
    summary = client.get("/api/summary").json()
    assert summary["total_orders"] == 40 and summary["revenue_at_risk"] == 3564.65


def test_csv_parser_and_stream_limits_return_useful_errors(client):
    oversized_field = ",".join(ORDER_COLUMNS) + "\n" + csv_order(region="x" * 140_000)
    assert client.post("/api/import", content=oversized_field).status_code == 422
    chunks = iter([b"a" * 600_000, b"b" * 500_000])
    assert client.post("/api/import", content=chunks).status_code == 413


def test_history_tracks_real_changes_and_survives_reimport_and_restart(tmp_path):
    database = tmp_path / "history.db"
    with TestClient(create_app(database_path=database)) as client:
        assert client.get("/api/exceptions/1005/history").json() == {"items": [], "total": 0}
        client.patch("/api/exceptions/1005", json={"status": "Investigating", "notes": " Carrier contacted. "})
        client.patch("/api/exceptions/1005", json={"status": "Investigating", "notes": "Carrier contacted."})
        client.patch("/api/exceptions/1005", json={"notes": ""})
        client.patch("/api/exceptions/1005", json={"status": "Resolved"})
        assert client.post("/api/import", content=SEED_CSV.read_text()).status_code == 200

    with TestClient(create_app(database_path=database)) as client:
        response = client.get("/api/exceptions/1005/history?limit=2").json()
        assert response["total"] == 3
        latest, cleared = response["items"]
        assert latest["previous_status"] == "Investigating" and latest["status"] == "Resolved"
        assert cleared["previous_notes"] == "Carrier contacted." and cleared["notes"] == ""
        first = client.get("/api/exceptions/1005/history?limit=2&offset=2").json()["items"][0]
        assert first["previous_status"] == "Open" and first["status"] == "Investigating"
        assert first["previous_notes"] == "" and first["notes"] == "Carrier contacted."
        assert first["created_at"].endswith("Z")
        assert client.get("/api/exceptions?q=1005").json()["items"][0]["status"] == "Resolved"
        assert client.get("/api/exceptions/9999/history").status_code == 404
