"""
agent/tools.py — All tools available to the autonomous diagnostic agent.

Each function is called by the LLM via Anthropic's tool-use API.
Tools interact with SQLite for history, run statistical analysis,
and execute corrective actions with full audit logging.
"""
import aiosqlite
import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

import numpy as np
from scipy import stats

from db.database import get_db_path
from db.models import AnomalyReport, Severity

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Tool 1: Query Sensor History
# ──────────────────────────────────────────────────────────────

async def query_sensor_history(
    sensor_id: str,
    time_range_seconds: int = 300,
    limit: int = 500
) -> Dict[str, Any]:
    """
    Fetch historical sensor readings from SQLite.

    Args:
        sensor_id: The sensor identifier (e.g., "TEMP_MOTOR")
        time_range_seconds: How far back to look (default: 5 minutes)
        limit: Maximum number of readings to return

    Returns:
        Dict with readings list, stats summary, and metadata
    """
    since = time.time() - time_range_seconds

    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT sensor_id, sensor_type, value, unit, timestamp, is_fault, fault_type
            FROM sensor_readings
            WHERE sensor_id = ? AND timestamp >= ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (sensor_id, since, limit))
        rows = await cursor.fetchall()

    if not rows:
        return {
            "sensor_id": sensor_id,
            "error": f"No data found for sensor '{sensor_id}' in last {time_range_seconds}s",
            "readings": [],
            "stats": {}
        }

    readings = [dict(r) for r in rows]
    values   = [r["value"] for r in readings]
    
    return {
        "sensor_id":      sensor_id,
        "sensor_type":    readings[0]["sensor_type"],
        "unit":           readings[0]["unit"],
        "count":          len(readings),
        "time_range_s":   time_range_seconds,
        "readings":       readings[:50],   # cap payload size
        "stats": {
            "mean":    round(float(np.mean(values)), 4),
            "std":     round(float(np.std(values)), 4),
            "min":     round(float(np.min(values)), 4),
            "max":     round(float(np.max(values)), 4),
            "median":  round(float(np.median(values)), 4),
            "p95":     round(float(np.percentile(values, 95)), 4),
            "p5":      round(float(np.percentile(values, 5)), 4),
        },
        "fault_count": sum(1 for r in readings if r["is_fault"]),
    }


# ──────────────────────────────────────────────────────────────
# Tool 2: Detect Anomaly
# ──────────────────────────────────────────────────────────────

# Sensor-specific operational thresholds (from hardware datasheets)
SENSOR_THRESHOLDS = {
    "TEMP_MCU":    {"min": 0,   "max": 85,  "critical_max": 100},
    "TEMP_MOTOR":  {"min": 0,   "max": 85,  "critical_max": 105},
    "VIB_BEARING": {"min": 0,   "max": 3.0, "critical_max": 6.0},
    "VOLT_12V":    {"min": 11.0,"max": 13.0,"critical_max": 14.5},
    "CURR_MOTOR":  {"min": 0,   "max": 10.0,"critical_max": 15.0},
}


async def detect_anomaly(sensor_id: str, window_seconds: int = 120) -> Dict[str, Any]:
    """
    Run multi-method statistical anomaly detection on a sensor.

    Methods:
      1. Z-score (modified, robust to outliers)
      2. IQR fence (non-parametric)
      3. Hardware threshold check (datasheet limits)

    Returns confidence score 0.0–1.0 and severity classification.
    """
    history = await query_sensor_history(sensor_id, time_range_seconds=window_seconds)

    if not history.get("readings"):
        return {
            "sensor_id":  sensor_id,
            "detected":   False,
            "error":      "Insufficient data",
            "score":      0.0,
            "severity":   "low",
            "methods":    {}
        }

    values  = [r["value"] for r in history["readings"]]
    current = values[0]   # most recent
    arr     = np.array(values)

    results  = {}
    scores   = []

    # ── Method 1: Modified Z-score (robust) ──
    median   = np.median(arr)
    mad      = np.median(np.abs(arr - median))
    mad      = mad if mad > 0 else 1e-6
    mz_score = 0.6745 * abs(current - median) / mad
    mz_anom  = bool(mz_score > 3.5)
    results["zscore"] = {
        "score":     round(float(mz_score), 3),
        "threshold": 3.5,
        "anomaly":   mz_anom
    }
    scores.append(min(1.0, mz_score / 10.0))

    # ── Method 2: IQR fence ──
    q1, q3   = np.percentile(arr, [25, 75])
    iqr      = q3 - q1
    fence_lo = q1 - 1.5 * iqr
    fence_hi = q3 + 1.5 * iqr
    iqr_anom = bool(current < fence_lo or current > fence_hi)
    iqr_dist = max(0, max(current - fence_hi, fence_lo - current)) / (iqr + 1e-6)
    results["iqr"] = {
        "lower_fence": round(float(fence_lo), 4),
        "upper_fence": round(float(fence_hi), 4),
        "anomaly":     iqr_anom,
        "distance":    round(float(iqr_dist), 3)
    }
    scores.append(min(1.0, iqr_dist / 3.0))

    # ── Method 3: Hardware threshold ──
    thresholds  = SENSOR_THRESHOLDS.get(sensor_id, {"min": -1e9, "max": 1e9, "critical_max": 1e9})
    thresh_anom = bool(current > thresholds["max"] or current < thresholds["min"])
    critical    = bool(current > thresholds["critical_max"])
    thresh_score = 0.0
    if thresh_anom:
        thresh_score = 0.6
    if critical:
        thresh_score = 1.0
    results["threshold"] = {
        "limits":  thresholds,
        "anomaly": thresh_anom,
        "critical": critical,
        "score":   thresh_score
    }
    scores.append(thresh_score)

    # ── Ensemble score (weighted) ──
    weights   = [0.3, 0.3, 0.4]   # threshold gets more weight for embedded systems
    ensemble  = sum(s * w for s, w in zip(scores, weights))
    detected  = bool(ensemble > 0.35 or critical)

    # ── Severity classification ──
    if ensemble > 0.8 or critical:
        severity = Severity.CRITICAL
    elif ensemble > 0.5:
        severity = Severity.HIGH
    elif ensemble > 0.3:
        severity = Severity.MEDIUM
    else:
        severity = Severity.LOW

    # ── Human-readable description ──
    desc_parts = []
    if mz_anom:
        desc_parts.append(f"modified Z-score={mz_score:.2f} (>3.5)")
    if iqr_anom:
        desc_parts.append(f"outside IQR fence [{fence_lo:.2f}, {fence_hi:.2f}]")
    if thresh_anom:
        desc_parts.append(f"exceeds hardware limit {thresholds['max']}")
    description = "; ".join(desc_parts) if desc_parts else "within normal operating range"

    return {
        "sensor_id":     sensor_id,
        "detected":      detected,
        "score":         round(ensemble, 4),
        "severity":      severity.value,
        "current_value": round(current, 4),
        "mean":          round(float(np.mean(arr)), 4),
        "std":           round(float(np.std(arr)), 4),
        "methods":       results,
        "description":   description,
        "timestamp":     time.time()
    }


# ──────────────────────────────────────────────────────────────
# Tool 3: Diagnose Fault
# ──────────────────────────────────────────────────────────────

async def diagnose_fault(
    sensor_id:      str,
    sensor_data:    Dict[str, Any],
    anomaly_report: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Perform structured root-cause analysis based on sensor data and anomaly findings.
    
    This tool encapsulates domain knowledge about embedded system fault modes.
    Returns a structured diagnosis with root cause, confidence, and recommended actions.
    """
    if not anomaly_report.get("detected"):
        return {
            "sensor_id":   sensor_id,
            "root_cause":  "No anomaly detected",
            "confidence":  0.1,
            "fault_class": "normal",
            "actions":     [],
        }

    current = anomaly_report.get("current_value", 0)
    score   = anomaly_report.get("score", 0)
    stype   = sensor_data.get("sensor_type", "")
    stats_  = sensor_data.get("stats", {})
    mean    = stats_.get("mean", 0)
    std     = stats_.get("std", 0)

    # Rule-based fault classification (maps to embedded failure modes)
    fault_class  = "unknown"
    root_cause   = "Undetermined fault"
    confidence   = score
    rec_actions  = []

    if stype == "temperature":
        if current > 90:
            fault_class = "thermal_runaway"
            root_cause  = (
                f"Thermal runaway detected on {sensor_id}. Temperature {current:.1f}°C "
                f"exceeds safe operating limit (85°C). Possible causes: "
                f"blocked cooling path, fan failure, or motor winding short circuit."
            )
            rec_actions = [
                "emergency_shutdown",
                "activate_cooling",
                "alert_operator",
                "log_thermal_event"
            ]
        elif current > 80:
            fault_class = "thermal_warning"
            root_cause  = (
                f"Elevated temperature on {sensor_id} ({current:.1f}°C). "
                f"Approaching thermal limit. Check cooling system efficiency."
            )
            rec_actions = ["reduce_load", "increase_fan_speed", "alert_operator"]
        elif std < 0.05 and current < 5:
            fault_class = "sensor_dropout"
            root_cause  = (
                f"Sensor dropout on {sensor_id}. Flatline reading at {current:.2f}°C "
                f"with near-zero variance (std={std:.4f}). Likely I2C/SPI bus fault "
                f"or NTC thermistor open circuit."
            )
            rec_actions = ["flag_sensor_fault", "switch_to_backup_sensor", "alert_operator"]

    elif stype == "vibration":
        if current > 4.0:
            fault_class = "motor_bearing_fault"
            root_cause  = (
                f"Motor bearing fault on {sensor_id}. Vibration amplitude {current:.3f}g "
                f"(baseline mean={mean:.3f}g). Characteristic of ball bearing degradation "
                f"or rotor imbalance. Immediate inspection recommended."
            )
            rec_actions = ["reduce_motor_speed", "schedule_maintenance", "alert_operator"]
        elif current > 2.5:
            fault_class = "vibration_warning"
            root_cause  = (
                f"Elevated vibration on {sensor_id} ({current:.3f}g vs mean {mean:.3f}g). "
                f"Early-stage bearing wear or mechanical looseness."
            )
            rec_actions = ["increase_monitoring_rate", "log_vibration_event"]

    elif stype == "voltage":
        if current < 11.0:
            fault_class = "power_supply_instability"
            root_cause  = (
                f"Voltage dropout on {sensor_id}. Rail voltage {current:.3f}V below "
                f"minimum spec (11.0V). Possible causes: DC-DC converter fault, "
                f"battery depletion, or excessive load demand."
            )
            rec_actions = ["switch_to_backup_power", "reduce_load", "alert_operator"]
        elif std > 0.3:
            fault_class = "voltage_ripple"
            root_cause  = (
                f"Voltage instability on {sensor_id}. High ripple detected (std={std:.3f}V). "
                f"Possible EMI interference or failing filter capacitor."
            )
            rec_actions = ["check_power_filter", "log_voltage_event"]

    elif stype == "current":
        if current > 12.0:
            fault_class = "overcurrent_event"
            root_cause  = (
                f"Overcurrent event on {sensor_id}. Load current {current:.2f}A exceeds "
                f"rated limit (10A). Motor may be stalled or short circuit present. "
                f"Risk of winding damage and thermal failure."
            )
            rec_actions = ["emergency_shutdown", "check_motor_load", "alert_operator"]
        elif current > 9.0:
            fault_class = "overload_warning"
            root_cause  = (
                f"Motor overload on {sensor_id} ({current:.2f}A). Operating above rated "
                f"current (8A). Sustained operation risks insulation damage."
            )
            rec_actions = ["reduce_load", "monitor_closely", "alert_operator"]

    return {
        "sensor_id":       sensor_id,
        "fault_class":     fault_class,
        "root_cause":      root_cause,
        "confidence":      round(confidence, 4),
        "current_value":   current,
        "recommended_actions": rec_actions,
        "severity":        anomaly_report.get("severity"),
        "timestamp":       time.time()
    }


# ──────────────────────────────────────────────────────────────
# Tool 4: Trigger Corrective Action
# ──────────────────────────────────────────────────────────────

# Action registry — maps action_type to simulated handler
ACTION_REGISTRY = {
    "emergency_shutdown": {
        "description": "Emergency shutdown of motor driver",
        "simulated_effect": "PWM disabled, brake applied",
        "severity_threshold": "critical"
    },
    "activate_cooling": {
        "description": "Increase cooling fan to 100%",
        "simulated_effect": "Fan PWM set to 100%",
        "severity_threshold": "high"
    },
    "reduce_load": {
        "description": "Reduce motor load by 30%",
        "simulated_effect": "Setpoint reduced to 70%",
        "severity_threshold": "medium"
    },
    "increase_fan_speed": {
        "description": "Increase cooling fan to 80%",
        "simulated_effect": "Fan PWM set to 80%",
        "severity_threshold": "medium"
    },
    "reduce_motor_speed": {
        "description": "Reduce motor RPM by 25%",
        "simulated_effect": "Speed setpoint reduced",
        "severity_threshold": "medium"
    },
    "switch_to_backup_power": {
        "description": "Switch to backup UPS rail",
        "simulated_effect": "Relay K1 switched to UPS",
        "severity_threshold": "critical"
    },
    "schedule_maintenance": {
        "description": "Flag unit for bearing replacement",
        "simulated_effect": "Maintenance ticket created",
        "severity_threshold": "medium"
    },
    "flag_sensor_fault": {
        "description": "Mark sensor as unreliable in system",
        "simulated_effect": "Sensor disabled in FMEA table",
        "severity_threshold": "low"
    },
    "switch_to_backup_sensor": {
        "description": "Route readings from backup sensor",
        "simulated_effect": "SPI MUX switched to channel 2",
        "severity_threshold": "medium"
    },
    "alert_operator": {
        "description": "Send alert to operator console",
        "simulated_effect": "Alert broadcast to dashboard",
        "severity_threshold": "low"
    },
    "increase_monitoring_rate": {
        "description": "Increase sensor polling to 10Hz",
        "simulated_effect": "Timer reload updated",
        "severity_threshold": "low"
    },
    "log_thermal_event": {
        "description": "Log thermal event to black-box recorder",
        "simulated_effect": "Written to EEPROM log",
        "severity_threshold": "low"
    },
    "log_vibration_event": {
        "description": "Log vibration event",
        "simulated_effect": "Written to event log",
        "severity_threshold": "low"
    },
    "log_voltage_event": {
        "description": "Log voltage event",
        "simulated_effect": "Written to event log",
        "severity_threshold": "low"
    },
    "check_power_filter": {
        "description": "Run power filter diagnostic",
        "simulated_effect": "Capacitor ESR check initiated",
        "severity_threshold": "medium"
    },
    "check_motor_load": {
        "description": "Run motor impedance check",
        "simulated_effect": "Motor impedance measurement started",
        "severity_threshold": "medium"
    },
    "monitor_closely": {
        "description": "Increase alert sensitivity for this sensor",
        "simulated_effect": "Alert threshold reduced by 20%",
        "severity_threshold": "low"
    }
}


async def trigger_corrective_action(
    action_type: str,
    incident_id: str,
    context:     Dict[str, Any] = {}
) -> Dict[str, Any]:
    """
    Execute and log a corrective action.
    
    In a real system this would send CAN bus commands or REST calls
    to the embedded controller. Here we simulate execution and log
    everything to the audit table.
    """
    action_info = ACTION_REGISTRY.get(action_type)
    if not action_info:
        return {
            "success":     False,
            "action_type": action_type,
            "error":       f"Unknown action type: {action_type}",
        }

    # Simulate execution delay (real: CAN/UART round-trip)
    await asyncio.sleep(0.1)

    executed_at = time.time()
    result = {
        "success":          True,
        "action_type":      action_type,
        "description":      action_info["description"],
        "simulated_effect": action_info["simulated_effect"],
        "incident_id":      incident_id,
        "executed_at":      executed_at,
    }

    # Persist to audit log
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute("""
            INSERT INTO corrective_actions (incident_id, action_type, action_data, executed_at, success)
            VALUES (?, ?, ?, ?, 1)
        """, (incident_id, action_type, json.dumps(context), executed_at))
        await db.commit()

    logger.info("⚡ Action executed: %s for incident %s", action_type, incident_id)
    return result


# ──────────────────────────────────────────────────────────────
# Tool 5: Generate Report
# ──────────────────────────────────────────────────────────────

async def generate_report(incident_id: str) -> Dict[str, Any]:
    """
    Generate a structured incident report by aggregating all data
    for the given incident_id from the database.
    """
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row

        # Fetch incident
        cur = await db.execute(
            "SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)
        )
        incident = await cur.fetchone()

        # Fetch agent steps
        cur = await db.execute(
            "SELECT * FROM agent_steps WHERE incident_id = ? ORDER BY timestamp",
            (incident_id,)
        )
        steps = await cur.fetchall()

        # Fetch corrective actions
        cur = await db.execute(
            "SELECT * FROM corrective_actions WHERE incident_id = ? ORDER BY executed_at",
            (incident_id,)
        )
        actions = await cur.fetchall()

    if not incident:
        return {"error": f"Incident {incident_id} not found"}

    inc  = dict(incident)
    report = {
        "report_id":     str(uuid.uuid4()),
        "incident_id":   incident_id,
        "generated_at":  time.time(),
        "summary": {
            "sensor_id":   inc["sensor_id"],
            "fault_type":  inc["fault_type"],
            "severity":    inc["severity"],
            "status":      inc["status"],
            "detected_at": inc["detected_at"],
            "resolved_at": inc.get("resolved_at"),
            "duration_s":  (
                (inc.get("resolved_at") or time.time()) - inc["detected_at"]
            )
        },
        "diagnosis":  inc.get("diagnosis"),
        "agent_reasoning_steps": len(steps),
        "corrective_actions": [
            {
                "action": dict(a)["action_type"],
                "executed_at": dict(a)["executed_at"],
                "success": bool(dict(a)["success"])
            }
            for a in actions
        ],
        "recommendations": [
            "Review sensor calibration schedule",
            "Update FMEA table with this fault mode",
            "Consider predictive maintenance interval reduction",
        ],
        "status": "complete"
    }

    # Persist report back to incident
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            "UPDATE incidents SET report = ?, status = 'resolved', resolved_at = ? WHERE incident_id = ?",
            (json.dumps(report), time.time(), incident_id)
        )
        await db.commit()

    return report


# ──────────────────────────────────────────────────────────────
# Tool Schema Definitions (for Claude API tool_use)
# ──────────────────────────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "query_sensor_history",
        "description": (
            "Fetch historical sensor readings from the time-series database. "
            "Use this to retrieve past data before running anomaly detection or diagnosis. "
            "Returns readings list, statistical summary (mean, std, min, max, percentiles), and fault count."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sensor_id": {
                    "type": "string",
                    "description": "Sensor identifier. One of: TEMP_MCU, TEMP_MOTOR, VIB_BEARING, VOLT_12V, CURR_MOTOR"
                },
                "time_range_seconds": {
                    "type": "integer",
                    "description": "How far back to look in seconds. Default 300 (5 min). Max 3600.",
                    "default": 300
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of readings to return. Default 500.",
                    "default": 500
                }
            },
            "required": ["sensor_id"]
        }
    },
    {
        "name": "detect_anomaly",
        "description": (
            "Run multi-method statistical anomaly detection on a sensor. "
            "Uses modified Z-score, IQR fencing, and hardware threshold checking. "
            "Returns a confidence score (0-1), severity level, and per-method breakdown. "
            "Call this after query_sensor_history when you suspect a fault."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sensor_id": {
                    "type": "string",
                    "description": "Sensor to analyze"
                },
                "window_seconds": {
                    "type": "integer",
                    "description": "Analysis window in seconds. Default 120.",
                    "default": 120
                }
            },
            "required": ["sensor_id"]
        }
    },
    {
        "name": "diagnose_fault",
        "description": (
            "Perform structured root-cause analysis based on sensor data and anomaly report. "
            "Returns fault classification, root cause description, confidence, and recommended corrective actions. "
            "Call this after detect_anomaly when an anomaly is confirmed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sensor_id": {
                    "type": "string",
                    "description": "Sensor ID being diagnosed"
                },
                "sensor_data": {
                    "type": "object",
                    "description": "Output from query_sensor_history"
                },
                "anomaly_report": {
                    "type": "object",
                    "description": "Output from detect_anomaly"
                }
            },
            "required": ["sensor_id", "sensor_data", "anomaly_report"]
        }
    },
    {
        "name": "trigger_corrective_action",
        "description": (
            "Execute a corrective action for a diagnosed fault. "
            "Actions are logged to the audit trail. "
            "Available actions: emergency_shutdown, activate_cooling, reduce_load, "
            "increase_fan_speed, reduce_motor_speed, switch_to_backup_power, "
            "schedule_maintenance, flag_sensor_fault, switch_to_backup_sensor, "
            "alert_operator, increase_monitoring_rate, log_thermal_event, "
            "log_vibration_event, log_voltage_event, check_power_filter, "
            "check_motor_load, monitor_closely. "
            "Always call alert_operator for high/critical severity events."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "description": "The corrective action to execute"
                },
                "incident_id": {
                    "type": "string",
                    "description": "Incident identifier for audit logging"
                },
                "context": {
                    "type": "object",
                    "description": "Optional additional context about why this action is being taken",
                    "default": {}
                }
            },
            "required": ["action_type", "incident_id"]
        }
    },
    {
        "name": "generate_report",
        "description": (
            "Generate a structured incident report for a completed diagnostic cycle. "
            "Aggregates all agent steps, corrective actions, and diagnosis into a final report. "
            "Call this as the last step after all corrective actions have been executed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "incident_id": {
                    "type": "string",
                    "description": "The incident ID to generate a report for"
                }
            },
            "required": ["incident_id"]
        }
    }
]

# Map tool names to async functions
TOOL_FUNCTIONS = {
    "query_sensor_history":     query_sensor_history,
    "detect_anomaly":           detect_anomaly,
    "diagnose_fault":           diagnose_fault,
    "trigger_corrective_action": trigger_corrective_action,
    "generate_report":          generate_report,
}