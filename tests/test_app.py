from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


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
