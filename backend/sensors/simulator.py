"""
sensors/simulator.py — IoT Sensor Stream Simulator

Mimics real STM32 embedded sensor behavior:
  - Temperature (NTC thermistor + ADC noise model)
  - Vibration (accelerometer FFT amplitude)
  - Voltage (12V rail with ripple)
  - Current (hall-effect sensor)

Supports fault injection scenarios matching real embedded faults.
"""
import asyncio
import time
import math
import random
import logging
from typing import Dict, Optional, AsyncGenerator
from dataclasses import dataclass, field
from db.models import SensorReading, SensorType, FaultType

logger = logging.getLogger(__name__)


@dataclass
class SensorConfig:
    sensor_id:   str
    sensor_type: SensorType
    unit:        str
    nominal_min: float
    nominal_max: float
    noise_std:   float
    update_hz:   float          # samples per second


# Default sensor fleet — mimics a motor controller + PSU system
DEFAULT_SENSORS: Dict[str, SensorConfig] = {
    "TEMP_MCU":    SensorConfig("TEMP_MCU",    SensorType.TEMPERATURE, "°C",  25.0,  75.0,  0.5, 1.0),
    "TEMP_MOTOR":  SensorConfig("TEMP_MOTOR",  SensorType.TEMPERATURE, "°C",  30.0,  80.0,  1.0, 1.0),
    "VIB_BEARING": SensorConfig("VIB_BEARING", SensorType.VIBRATION,   "g",   0.1,   2.0,  0.05, 2.0),
    "VOLT_12V":    SensorConfig("VOLT_12V",    SensorType.VOLTAGE,     "V",  11.5,  12.5,  0.05, 2.0),
    "CURR_MOTOR":  SensorConfig("CURR_MOTOR",  SensorType.CURRENT,     "A",   1.0,   8.0,  0.1,  2.0),
}


@dataclass
class FaultScenario:
    fault_type:  FaultType
    sensor_id:   str
    duration_s:  float
    magnitude:   float          # multiplier or offset for fault
    description: str


# Realistic fault scenarios based on embedded systems failure modes
FAULT_SCENARIOS = [
    FaultScenario(FaultType.THERMAL_RUNAWAY,         "TEMP_MOTOR",  30.0, 1.8,  "Motor winding thermal runaway"),
    FaultScenario(FaultType.MOTOR_BEARING_FAULT,     "VIB_BEARING", 25.0, 4.5,  "Ball bearing degradation"),
    FaultScenario(FaultType.POWER_SUPPLY_INSTABILITY,"VOLT_12V",    20.0, 0.7,  "DC-DC converter dropout"),
    FaultScenario(FaultType.OVERCURRENT_EVENT,       "CURR_MOTOR",  15.0, 2.5,  "Motor stall overcurrent"),
    FaultScenario(FaultType.SENSOR_DROPOUT,          "TEMP_MCU",    10.0, 1.0,  "I2C sensor flatline"),
]


class SensorSimulator:
    """
    Async sensor simulator with realistic noise models and fault injection.
    Each sensor evolves with Brownian-ish motion within its nominal range,
    plus periodic fault injection for agent training scenarios.
    """

    def __init__(self, sensors: Dict[str, SensorConfig] = DEFAULT_SENSORS):
        self._sensors  = sensors
        self._state:   Dict[str, float] = {}          # current value per sensor
        self._faults:  Dict[str, FaultScenario] = {}  # active faults per sensor
        self._fault_start: Dict[str, float] = {}
        self._running  = False
        self._callbacks = []

        # Initialize state at nominal midpoint
        for sid, cfg in sensors.items():
            self._state[sid] = (cfg.nominal_min + cfg.nominal_max) / 2.0

    def add_callback(self, cb):
        """Register async callback called with each new SensorReading."""
        self._callbacks.append(cb)

    async def _notify(self, reading: SensorReading):
        for cb in self._callbacks:
            try:
                await cb(reading)
            except Exception as e:
                logger.error("Sensor callback error: %s", e)

    def inject_fault(self, scenario: FaultScenario):
        """Manually inject a fault scenario (also called by auto-scheduler)."""
        sid = scenario.sensor_id
        self._faults[sid]      = scenario
        self._fault_start[sid] = time.time()
        logger.warning("🔴 FAULT INJECTED: %s on %s", scenario.fault_type, sid)

    def clear_fault(self, sensor_id: str):
        """Clear an active fault."""
        if sensor_id in self._faults:
            del self._faults[sensor_id]
            del self._fault_start[sensor_id]
            logger.info("✅ Fault cleared on %s", sensor_id)

    def _next_value(self, sensor_id: str) -> tuple[float, bool, Optional[FaultType]]:
        """
        Generate next sensor reading with noise model.
        Returns (value, is_fault, fault_type).
        """
        cfg     = self._sensors[sensor_id]
        current = self._state[sensor_id]

        # Brownian motion drift — keeps values realistic
        drift  = random.gauss(0, cfg.noise_std)
        target = (cfg.nominal_min + cfg.nominal_max) / 2.0
        mean_reversion = 0.02 * (target - current)   # soft pull to center
        new_val = current + drift + mean_reversion

        # Clamp to a safe range (2x nominal)
        new_val = max(cfg.nominal_min * 0.5, min(cfg.nominal_max * 2.0, new_val))

        is_fault  = False
        fault_type = None

        # Apply active fault if present
        if sensor_id in self._faults:
            scenario   = self._faults[sensor_id]
            elapsed    = time.time() - self._fault_start[sensor_id]

            if elapsed > scenario.duration_s:
                self.clear_fault(sensor_id)
            else:
                is_fault   = True
                fault_type = scenario.fault_type
                progress   = elapsed / scenario.duration_s   # 0→1

                if scenario.fault_type == FaultType.THERMAL_RUNAWAY:
                    # Exponential ramp
                    new_val = current + (cfg.nominal_max * scenario.magnitude * 0.1 * progress)

                elif scenario.fault_type == FaultType.MOTOR_BEARING_FAULT:
                    # Sinusoidal vibration spike
                    new_val = cfg.nominal_max * scenario.magnitude * abs(math.sin(elapsed * 8))

                elif scenario.fault_type == FaultType.POWER_SUPPLY_INSTABILITY:
                    # Voltage sag with ripple
                    new_val = (cfg.nominal_min + cfg.nominal_max) / 2 * scenario.magnitude
                    new_val += random.gauss(0, cfg.noise_std * 5)

                elif scenario.fault_type == FaultType.OVERCURRENT_EVENT:
                    # Step change + ramp
                    new_val = cfg.nominal_max * scenario.magnitude * (0.8 + 0.2 * progress)

                elif scenario.fault_type == FaultType.SENSOR_DROPOUT:
                    # Flatline at last known value with tiny jitter
                    new_val = cfg.nominal_min + random.gauss(0, 0.01)

        self._state[sensor_id] = new_val
        return round(new_val, 4), is_fault, fault_type

    async def _sensor_loop(self, sensor_id: str):
        """Async loop for a single sensor at its configured sample rate."""
        cfg      = self._sensors[sensor_id]
        interval = 1.0 / cfg.update_hz

        while self._running:
            value, is_fault, fault_type = self._next_value(sensor_id)

            reading = SensorReading(
                sensor_id   = sensor_id,
                sensor_type = cfg.sensor_type,
                value       = value,
                unit        = cfg.unit,
                timestamp   = time.time(),
                is_fault    = is_fault,
                fault_type  = fault_type,
            )

            await self._notify(reading)
            await asyncio.sleep(interval)

    async def _fault_scheduler(self):
        """
        Periodically injects random fault scenarios to keep the agent busy.
        In a real system this is replaced by actual hardware fault detection.
        """
        while self._running:
            # Wait 45–90 seconds between fault injections
            await asyncio.sleep(random.uniform(45, 90))

            if not self._running:
                break

            # Only inject if no active faults
            if not self._faults:
                scenario = random.choice(FAULT_SCENARIOS)
                self.inject_fault(scenario)

    async def start(self):
        """Start all sensor loops and fault scheduler as concurrent tasks."""
        self._running = True
        tasks = [
            asyncio.create_task(self._sensor_loop(sid), name=f"sensor_{sid}")
            for sid in self._sensors
        ]
        tasks.append(asyncio.create_task(self._fault_scheduler(), name="fault_scheduler"))
        logger.info("🟢 Sensor simulator started with %d sensors", len(self._sensors))
        return tasks

    async def stop(self):
        self._running = False
        logger.info("Sensor simulator stopped")

    def get_current_state(self) -> Dict[str, float]:
        return dict(self._state)

    def get_active_faults(self) -> Dict[str, str]:
        return {sid: sc.fault_type.value for sid, sc in self._faults.items()}