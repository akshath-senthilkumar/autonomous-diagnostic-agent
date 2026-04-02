"""
tests/test_api.py — Integration tests for FastAPI REST endpoints.
Run with: pytest tests/test_api.py -v
"""
import os
import pytest
import time
import aiosqlite

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from httpx import AsyncClient, ASGITransport


@pytest.fixture(autouse=True)
async def setup_db(tmp_path, monkeypatch):
    import db.database as dbmod
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(dbmod, "DATABASE_PATH", db_path)
    from db.database import init_db
    await init_db()


@pytest.fixture
async def client(tmp_path, monkeypatch):
    import db.database as dbmod
    db_path = tmp_path / "test_api.db"
    monkeypatch.setattr(dbmod, "DATABASE_PATH", db_path)
    from db.database import init_db
    await init_db()

    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_root(client):
    r = await client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "online"


@pytest.mark.asyncio
async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_list_sensors(client):
    r = await client.get("/api/v1/sensors")
    assert r.status_code == 200
    sensors = r.json()
    assert "TEMP_MCU" in sensors
    assert "VOLT_12V" in sensors


@pytest.mark.asyncio
async def test_system_status(client):
    r = await client.get("/api/v1/status")
    assert r.status_code == 200
    data = r.json()
    assert "status" in data
    assert "reading_count" in data


@pytest.mark.asyncio
async def test_sensor_history_not_found(client):
    r = await client.get("/api/v1/sensors/FAKE_SENSOR/history")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_incidents_empty(client):
    r = await client.get("/api/v1/incidents")
    assert r.status_code == 200
    assert r.json()["incidents"] == []


@pytest.mark.asyncio
async def test_incident_not_found(client):
    r = await client.get("/api/v1/incidents/NONEXISTENT")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_fault_scenarios(client):
    r = await client.get("/api/v1/fault-scenarios")
    assert r.status_code == 200
    data = r.json()
    assert len(data["scenarios"]) > 0
    for s in data["scenarios"]:
        assert "fault_type" in s
        assert "sensor_id" in s


@pytest.mark.asyncio
async def test_inject_fault_invalid(client):
    r = await client.post("/api/v1/inject-fault", json={})
    assert r.status_code in (400, 422, 503)


@pytest.mark.asyncio
async def test_full_incident_lifecycle(client):
    """Insert incident, read it back, verify structure."""
    from db.database import get_db_path
    incident_id = "INC-APITEST-001"

    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute("""
            INSERT INTO incidents
                (incident_id, sensor_id, fault_type, severity, status, detected_at)
            VALUES (?, 'TEMP_MOTOR', 'thermal_runaway', 'critical', 'investigating', ?)
        """, (incident_id, time.time()))
        await db.commit()

    r = await client.get("/api/v1/incidents")
    assert r.status_code == 200
    incidents = r.json()["incidents"]
    assert any(i["incident_id"] == incident_id for i in incidents)

    r = await client.get(f"/api/v1/incidents/{incident_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["incident"]["incident_id"] == incident_id
    assert "agent_steps" in data
    assert "corrective_actions" in data