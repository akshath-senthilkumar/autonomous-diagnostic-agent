"""
tests/test_agent.py — Unit tests for agent tools.
Run with: pytest tests/test_agent.py -v
"""
import asyncio
import os
import time
import pytest
import aiosqlite

# Point tests at an in-memory/temp DB
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used-in-unit-tests")

from db.database import init_db, get_db_path
from agent.tools import (
    query_sensor_history,
    detect_anomaly,
    diagnose_fault,
    trigger_corrective_action,
    generate_report,
    TOOL_DEFINITIONS,
    TOOL_FUNCTIONS,
    ACTION_REGISTRY,
)


@pytest.fixture(autouse=True)
async def setup_db(tmp_path, monkeypatch):
    """Use a temporary DB for each test."""
    import db.database as dbmod
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(dbmod, "DATABASE_PATH", db_path)
    await init_db()


@pytest.mark.asyncio
async def test_query_sensor_history_empty():
    result = await query_sensor_history("TEMP_MCU", time_range_seconds=60)
    assert "error" in result or result["count"] == 0


@pytest.mark.asyncio
async def test_query_sensor_history_with_data():
    # Insert test data
    async with aiosqlite.connect(get_db_path()) as db:
        now = time.time()
        for i in range(20):
            await db.execute("""
                INSERT INTO sensor_readings (sensor_id, sensor_type, value, unit, timestamp, is_fault)
                VALUES ('TEMP_MCU', 'temperature', ?, '°C', ?, 0)
            """, (50.0 + i * 0.5, now - i * 5))
        await db.commit()

    result = await query_sensor_history("TEMP_MCU", time_range_seconds=200)
    assert result["count"] == 20
    assert "stats" in result
    assert result["stats"]["mean"] > 0
    assert result["stats"]["min"] <= result["stats"]["max"]


@pytest.mark.asyncio
async def test_detect_anomaly_no_data():
    result = await detect_anomaly("TEMP_MCU")
    assert result["detected"] is False
    assert result["score"] == 0.0


@pytest.mark.asyncio
async def test_detect_anomaly_normal():
    # Insert normal temperature readings
    async with aiosqlite.connect(get_db_path()) as db:
        now = time.time()
        for i in range(30):
            await db.execute("""
                INSERT INTO sensor_readings (sensor_id, sensor_type, value, unit, timestamp, is_fault)
                VALUES ('TEMP_MCU', 'temperature', ?, '°C', ?, 0)
            """, (45.0 + (i % 3) * 0.5, now - i * 3))
        await db.commit()

    result = await detect_anomaly("TEMP_MCU", window_seconds=200)
    assert "detected" in result
    assert "score" in result
    assert 0.0 <= result["score"] <= 1.0


@pytest.mark.asyncio
async def test_detect_anomaly_critical():
    # Insert mostly normal, then spike to critical temperature
    async with aiosqlite.connect(get_db_path()) as db:
        now = time.time()
        for i in range(25):
            await db.execute("""
                INSERT INTO sensor_readings (sensor_id, sensor_type, value, unit, timestamp, is_fault)
                VALUES ('TEMP_MOTOR', 'temperature', ?, '°C', ?, 0)
            """, (50.0, now - i * 5))
        # Most recent: critical spike
        await db.execute("""
            INSERT INTO sensor_readings (sensor_id, sensor_type, value, unit, timestamp, is_fault)
            VALUES ('TEMP_MOTOR', 'temperature', 105.0, '°C', ?, 1)
        """, (now,))
        await db.commit()

    result = await detect_anomaly("TEMP_MOTOR", window_seconds=300)
    assert result["detected"] is True
    assert result["severity"] in ("high", "critical")
    assert result["score"] > 0.5


@pytest.mark.asyncio
async def test_diagnose_fault_no_anomaly():
    sensor_data    = {"sensor_type": "temperature", "stats": {"mean": 50, "std": 1}}
    anomaly_report = {"detected": False, "score": 0.1}
    result = await diagnose_fault("TEMP_MCU", sensor_data, anomaly_report)
    assert result["fault_class"] == "normal"
    assert result["confidence"] == 0.1


@pytest.mark.asyncio
async def test_diagnose_fault_thermal():
    sensor_data = {
        "sensor_type": "temperature",
        "stats": {"mean": 50, "std": 2}
    }
    anomaly_report = {
        "detected": True,
        "score":    0.9,
        "severity": "critical",
        "current_value": 102.0
    }
    result = await diagnose_fault("TEMP_MOTOR", sensor_data, anomaly_report)
    assert result["fault_class"] == "thermal_runaway"
    assert len(result["recommended_actions"]) > 0
    assert "emergency_shutdown" in result["recommended_actions"]


@pytest.mark.asyncio
async def test_trigger_corrective_action():
    # Create a dummy incident first
    incident_id = "INC-TEST-001"
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute("""
            INSERT INTO incidents (incident_id, sensor_id, fault_type, severity, status, detected_at)
            VALUES (?, 'TEMP_MOTOR', 'thermal_runaway', 'critical', 'investigating', ?)
        """, (incident_id, time.time()))
        await db.commit()

    result = await trigger_corrective_action("emergency_shutdown", incident_id)
    assert result["success"] is True
    assert result["action_type"] == "emergency_shutdown"

    # Verify it was logged
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM corrective_actions WHERE incident_id = ?", (incident_id,)
        )
        row = await cur.fetchone()
    assert row is not None
    assert dict(row)["action_type"] == "emergency_shutdown"


@pytest.mark.asyncio
async def test_trigger_unknown_action():
    result = await trigger_corrective_action("nonexistent_action", "INC-0")
    assert result["success"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_generate_report():
    incident_id = "INC-REPORT-001"
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute("""
            INSERT INTO incidents (incident_id, sensor_id, fault_type, severity, status, detected_at, diagnosis)
            VALUES (?, 'TEMP_MOTOR', 'thermal_runaway', 'critical', 'investigating', ?, 'Motor winding overheated')
        """, (incident_id, time.time()))
        await db.execute("""
            INSERT INTO corrective_actions (incident_id, action_type, executed_at, success)
            VALUES (?, 'emergency_shutdown', ?, 1)
        """, (incident_id, time.time()))
        await db.commit()

    report = await generate_report(incident_id)
    assert "incident_id" in report
    assert report["incident_id"] == incident_id
    assert "summary" in report
    assert len(report["corrective_actions"]) == 1


def test_all_tools_defined():
    """All tools in TOOL_DEFINITIONS have a corresponding function."""
    for tool in TOOL_DEFINITIONS:
        assert tool["name"] in TOOL_FUNCTIONS, f"Missing function for tool: {tool['name']}"


def test_tool_definitions_have_required_fields():
    for tool in TOOL_DEFINITIONS:
        assert "name" in tool
        assert "description" in tool
        assert "input_schema" in tool
        assert tool["input_schema"]["type"] == "object"


def test_all_actions_in_registry():
    important_actions = [
        "emergency_shutdown", "alert_operator", "reduce_load",
        "activate_cooling", "schedule_maintenance"
    ]
    for action in important_actions:
        assert action in ACTION_REGISTRY, f"Missing action: {action}"