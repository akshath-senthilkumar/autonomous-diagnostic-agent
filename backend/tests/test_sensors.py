"""
tests/test_sensors.py — Unit tests for the sensor simulator.
Run with: pytest tests/test_sensors.py -v
"""
import asyncio
import pytest
import time
from sensors.simulator import SensorSimulator, FAULT_SCENARIOS, DEFAULT_SENSORS
from db.models import SensorType, FaultType


@pytest.fixture
def sim():
    return SensorSimulator()


def test_simulator_initial_state(sim):
    state = sim.get_current_state()
    assert len(state) == len(DEFAULT_SENSORS)
    for sid, cfg in DEFAULT_SENSORS.items():
        assert sid in state
        val = state[sid]
        assert cfg.nominal_min <= val <= cfg.nominal_max, (
            f"{sid}: {val} not in [{cfg.nominal_min}, {cfg.nominal_max}]"
        )


def test_no_active_faults_initially(sim):
    assert sim.get_active_faults() == {}


def test_fault_injection(sim):
    scenario = FAULT_SCENARIOS[0]   # thermal runaway
    sim.inject_fault(scenario)
    active = sim.get_active_faults()
    assert scenario.sensor_id in active
    assert active[scenario.sensor_id] == scenario.fault_type.value


def test_fault_clear(sim):
    scenario = FAULT_SCENARIOS[0]
    sim.inject_fault(scenario)
    sim.clear_fault(scenario.sensor_id)
    assert sim.get_active_faults() == {}


def test_next_value_no_fault(sim):
    for sid in DEFAULT_SENSORS:
        val, is_fault, fault_type = sim._next_value(sid)
        assert isinstance(val, float)
        assert is_fault is False
        assert fault_type is None


def test_next_value_with_fault(sim):
    scenario = FAULT_SCENARIOS[0]   # TEMP_MOTOR thermal runaway
    sim.inject_fault(scenario)
    # Give fault time to propagate
    for _ in range(5):
        val, is_fault, fault_type = sim._next_value(scenario.sensor_id)
    assert is_fault is True


@pytest.mark.asyncio
async def test_callback_called(sim):
    received = []
    async def cb(reading):
        received.append(reading)

    sim.add_callback(cb)
    tasks = await sim.start()

    await asyncio.sleep(1.5)   # let a few readings through
    await sim.stop()
    for t in tasks:
        t.cancel()

    assert len(received) > 0
    for r in received:
        assert r.sensor_id in DEFAULT_SENSORS
        assert isinstance(r.value, float)


def test_all_fault_scenarios_have_valid_sensors():
    for scenario in FAULT_SCENARIOS:
        assert scenario.sensor_id in DEFAULT_SENSORS, (
            f"Fault scenario references unknown sensor: {scenario.sensor_id}"
        )
        assert isinstance(scenario.fault_type, FaultType)
        assert scenario.duration_s > 0
        assert scenario.magnitude > 0