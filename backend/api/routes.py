"""
api/routes.py — FastAPI REST API routes.

Provides endpoints for:
  - Sensor data queries
  - Incident management
  - Agent step audit trail
  - Manual fault injection
  - System status
"""
import aiosqlite
import json
import logging
import time
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from db.database import get_db_path
from sensors.simulator import FAULT_SCENARIOS, FaultScenario

logger = logging.getLogger(__name__)
router = APIRouter()

# Reference to simulator (injected at startup)
_simulator = None

def set_simulator(sim):
    global _simulator
    _simulator = sim


# ── Sensor Data ──────────────────────────────────────────────

@router.get("/sensors")
async def list_sensors():
    """List all sensor IDs and their types."""
    from sensors.simulator import DEFAULT_SENSORS
    return {
        sid: {
            "sensor_type": cfg.sensor_type,
            "unit":        cfg.unit,
            "nominal_min": cfg.nominal_min,
            "nominal_max": cfg.nominal_max,
            "update_hz":   cfg.update_hz,
        }
        for sid, cfg in DEFAULT_SENSORS.items()
    }


@router.get("/sensors/current")
async def current_readings():
    """Get the most recent reading for each sensor."""
    if not _simulator:
        raise HTTPException(503, "Simulator not initialized")
    return {
        "state":  _simulator.get_current_state(),
        "faults": _simulator.get_active_faults(),
        "timestamp": time.time()
    }


@router.get("/sensors/{sensor_id}/history")
async def sensor_history(
    sensor_id:    str,
    seconds:      int = Query(300,  ge=10, le=3600),
    limit:        int = Query(1000, ge=10, le=5000),
):
    """Get historical readings for a sensor."""
    since = time.time() - seconds
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT sensor_id, sensor_type, value, unit, timestamp, is_fault, fault_type
            FROM sensor_readings
            WHERE sensor_id = ? AND timestamp >= ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (sensor_id, since, limit))
        rows = await cur.fetchall()

    if not rows:
        raise HTTPException(404, f"No data found for sensor '{sensor_id}'")

    return {
        "sensor_id": sensor_id,
        "count":     len(rows),
        "readings":  [dict(r) for r in rows]
    }


# ── Incidents ────────────────────────────────────────────────

@router.get("/incidents")
async def list_incidents(
    status: Optional[str] = None,
    limit:  int = Query(50, ge=1, le=200),
):
    """List incidents, optionally filtered by status."""
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        if status:
            cur = await db.execute(
                "SELECT * FROM incidents WHERE status = ? ORDER BY detected_at DESC LIMIT ?",
                (status, limit)
            )
        else:
            cur = await db.execute(
                "SELECT * FROM incidents ORDER BY detected_at DESC LIMIT ?",
                (limit,)
            )
        rows = await cur.fetchall()

    return {"incidents": [dict(r) for r in rows]}


@router.get("/incidents/{incident_id}")
async def get_incident(incident_id: str):
    """Get full incident detail including agent steps and corrective actions."""
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row

        cur = await db.execute(
            "SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)
        )
        incident = await cur.fetchone()
        if not incident:
            raise HTTPException(404, f"Incident '{incident_id}' not found")

        cur = await db.execute(
            "SELECT * FROM agent_steps WHERE incident_id = ? ORDER BY timestamp",
            (incident_id,)
        )
        steps = await cur.fetchall()

        cur = await db.execute(
            "SELECT * FROM corrective_actions WHERE incident_id = ? ORDER BY executed_at",
            (incident_id,)
        )
        actions = await cur.fetchall()

    return {
        "incident":           dict(incident),
        "agent_steps":        [dict(s) for s in steps],
        "corrective_actions": [dict(a) for a in actions],
    }


@router.get("/incidents/{incident_id}/steps")
async def get_agent_steps(incident_id: str):
    """Get all agent reasoning steps for an incident."""
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM agent_steps WHERE incident_id = ? ORDER BY timestamp",
            (incident_id,)
        )
        rows = await cur.fetchall()

    return {"incident_id": incident_id, "steps": [dict(r) for r in rows]}


# ── Fault Injection ──────────────────────────────────────────

class FaultInjectRequest(BaseModel):
    scenario_index: Optional[int] = None   # 0–4 for preset scenarios
    sensor_id:      Optional[str] = None   # custom
    duration_s:     float = 30.0
    magnitude:      float = 2.0


@router.post("/inject-fault")
async def inject_fault(req: FaultInjectRequest):
    """Manually inject a fault scenario (for testing/demo)."""
    if not _simulator:
        raise HTTPException(503, "Simulator not initialized")

    from db.models import FaultType

    if req.scenario_index is not None:
        if req.scenario_index >= len(FAULT_SCENARIOS):
            raise HTTPException(400, "Invalid scenario index")
        scenario = FAULT_SCENARIOS[req.scenario_index]
    elif req.sensor_id:
        scenario = FaultScenario(
            fault_type  = FaultType.THERMAL_RUNAWAY,
            sensor_id   = req.sensor_id,
            duration_s  = req.duration_s,
            magnitude   = req.magnitude,
            description = "Manual fault injection"
        )
    else:
        raise HTTPException(400, "Provide scenario_index or sensor_id")

    _simulator.inject_fault(scenario)
    return {
        "injected":    True,
        "sensor_id":   scenario.sensor_id,
        "fault_type":  scenario.fault_type,
        "duration_s":  scenario.duration_s,
    }


@router.post("/clear-fault/{sensor_id}")
async def clear_fault(sensor_id: str):
    """Clear an active fault on a sensor."""
    if not _simulator:
        raise HTTPException(503, "Simulator not initialized")
    _simulator.clear_fault(sensor_id)
    return {"cleared": True, "sensor_id": sensor_id}


@router.get("/fault-scenarios")
async def list_fault_scenarios():
    """List all available preset fault scenarios."""
    return {
        "scenarios": [
            {
                "index":       i,
                "fault_type":  s.fault_type,
                "sensor_id":   s.sensor_id,
                "duration_s":  s.duration_s,
                "magnitude":   s.magnitude,
                "description": s.description,
            }
            for i, s in enumerate(FAULT_SCENARIOS)
        ]
    }


# ── System Status ─────────────────────────────────────────────

@router.get("/status")
async def system_status():
    """System health and statistics."""
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row

        cur = await db.execute("SELECT COUNT(*) as n FROM sensor_readings")
        reading_count = (await cur.fetchone())["n"]

        cur = await db.execute("SELECT COUNT(*) as n FROM incidents")
        incident_count = (await cur.fetchone())["n"]

        cur = await db.execute(
            "SELECT COUNT(*) as n FROM incidents WHERE status = 'investigating'"
        )
        open_count = (await cur.fetchone())["n"]

    active_faults = _simulator.get_active_faults() if _simulator else {}

    return {
        "status":          "online",
        "timestamp":       time.time(),
        "sensor_count":    len(_simulator._sensors) if _simulator else 0,
        "active_faults":   active_faults,
        "reading_count":   reading_count,
        "incident_count":  incident_count,
        "open_incidents":  open_count,
        "agent_model":     "gemini-1.5-flash",
    }