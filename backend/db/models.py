"""
db/models.py — Pydantic data models used across the system.
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from enum import Enum
import time


class SensorType(str, Enum):
    TEMPERATURE = "temperature"
    VIBRATION   = "vibration"
    VOLTAGE     = "voltage"
    CURRENT     = "current"


class FaultType(str, Enum):
    THERMAL_RUNAWAY        = "thermal_runaway"
    MOTOR_BEARING_FAULT    = "motor_bearing_fault"
    POWER_SUPPLY_INSTABILITY = "power_supply_instability"
    OVERCURRENT_EVENT      = "overcurrent_event"
    SENSOR_DROPOUT         = "sensor_dropout"
    NORMAL                 = "normal"


class Severity(str, Enum):
    LOW      = "low"
    MEDIUM   = "medium"
    HIGH     = "high"
    CRITICAL = "critical"


class SensorReading(BaseModel):
    sensor_id:   str
    sensor_type: SensorType
    value:       float
    unit:        str
    timestamp:   float = Field(default_factory=time.time)
    is_fault:    bool  = False
    fault_type:  Optional[FaultType] = None

    model_config = {"use_enum_values": True}


class AnomalyReport(BaseModel):
    sensor_id:       str
    detected:        bool
    score:           float             # 0.0–1.0 confidence
    method:          str               # "zscore" | "iqr" | "threshold"
    current_value:   float
    mean:            float
    std:             float
    threshold:       float
    description:     str
    severity:        Severity
    timestamp:       float = Field(default_factory=time.time)


class IncidentCreate(BaseModel):
    incident_id:     str
    sensor_id:       str
    fault_type:      str
    severity:        Severity
    detected_at:     float = Field(default_factory=time.time)
    anomaly_report:  Optional[str] = None


class Incident(BaseModel):
    id:               int
    incident_id:      str
    sensor_id:        str
    fault_type:       str
    severity:         str
    status:           str
    detected_at:      float
    resolved_at:      Optional[float]
    anomaly_report:   Optional[str]
    diagnosis:        Optional[str]
    corrective_action: Optional[str]
    report:           Optional[str]


class AgentStep(BaseModel):
    incident_id: str
    step_type:   str        # "tool_call" | "reasoning" | "complete"
    tool_name:   Optional[str]
    tool_input:  Optional[Dict[str, Any]]
    tool_output: Optional[str]
    reasoning:   Optional[str]
    timestamp:   float = Field(default_factory=time.time)


class CorrectiveAction(BaseModel):
    action_type: str
    description: str
    parameters:  Dict[str, Any] = {}


# WebSocket broadcast message types
class WSMessage(BaseModel):
    type:      str          # "sensor_data" | "anomaly" | "agent_step" | "incident" | "alert"
    payload:   Dict[str, Any]
    timestamp: float = Field(default_factory=time.time)