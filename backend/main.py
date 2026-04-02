"""
main.py — FastAPI application entry point.

Wires together:
  - Sensor simulator (background async tasks)
  - SQLite persistence layer
  - Autonomous diagnostic agent
  - REST API routes
  - WebSocket broadcasting
"""
import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager

import aiosqlite
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ── Local imports after env load ──
from db.database import init_db, get_db_path
from db.models import SensorReading
from sensors.simulator import SensorSimulator
from agent.agent_core import DiagnosticAgent, AnomalyMonitor
from api.routes import router as api_router, set_simulator
from api.websocket import manager


# ──────────────────────────────────────────────────────────────
# Application startup / shutdown
# ──────────────────────────────────────────────────────────────

simulator:  SensorSimulator | None = None
agent:      DiagnosticAgent | None = None
monitor:    AnomalyMonitor  | None = None
sim_tasks:  list = []


async def on_sensor_reading(reading: SensorReading):
    """
    Central callback for every sensor reading:
      1. Persist to SQLite
      2. Broadcast to WebSocket clients
      3. Check for anomalies → trigger agent if needed
    """
    # Persist
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute("""
            INSERT INTO sensor_readings
                (sensor_id, sensor_type, value, unit, timestamp, is_fault, fault_type)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            reading.sensor_id,
            reading.sensor_type,
            reading.value,
            reading.unit,
            reading.timestamp,
            int(reading.is_fault),
            reading.fault_type,
        ))
        await db.commit()

    # Broadcast to dashboard
    await manager.broadcast({
        "message_type": "sensor_update",
        "data": {
            "state":     simulator.get_current_state() if simulator else {},
            "faults":    simulator.get_active_faults() if simulator else {},
            "timestamp": time.time()
        }
    })

    # Anomaly check → maybe trigger agent
    if monitor:
        await monitor.on_reading(reading)


async def agent_step_callback(event: dict):
    """Broadcast agent events to all WebSocket clients."""
    await manager.broadcast({
        "type":    event.get("type", "agent_step"),
        "payload": event
    })


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan — startup then shutdown."""
    global simulator, agent, monitor, sim_tasks

    # 1. Init DB
    await init_db()

    # 2. Start sensor simulator
    simulator = SensorSimulator()
    simulator.add_callback(on_sensor_reading)
    set_simulator(simulator)

    # 3. Init agent + monitor
    agent   = DiagnosticAgent(step_callback=agent_step_callback)
    monitor = AnomalyMonitor(agent)

    # 4. Launch sensor loops
    sim_tasks = await simulator.start()
    logger.info("🚀 Embedded Diagnostic Agent online")

    yield

    # Shutdown
    await simulator.stop()
    for task in sim_tasks:
        task.cancel()
    logger.info("🛑 System shutdown complete")


# ──────────────────────────────────────────────────────────────
# FastAPI Application
# ──────────────────────────────────────────────────────────────

app = FastAPI(
    title       = "Autonomous Embedded Diagnostic Agent",
    description = "Real-time IoT sensor monitoring with autonomous AI fault diagnosis",
    version     = "1.0.0",
    lifespan    = lifespan,
)

# CORS for React dev server
origins = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins     = origins,
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# REST routes
app.include_router(api_router, prefix="/api/v1")


# ──────────────────────────────────────────────────────────────
# WebSocket endpoint
# ──────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)

    # Send initial state snapshot to new client
    if simulator:
        await manager.send_to(ws, {
            "type": "snapshot",
            "payload": {
                "state":     simulator.get_current_state(),
                "faults":    simulator.get_active_faults(),
                "timestamp": time.time(),
            }
        })

    try:
        while True:
            # Keep connection alive — handle any incoming messages
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
                # Handle ping/pong
                if msg.get("type") == "ping":
                    await manager.send_to(ws, {"type": "pong", "timestamp": time.time()})
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        manager.disconnect(ws)


# ──────────────────────────────────────────────────────────────
# Root health check
# ──────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "service":   "Autonomous Embedded Diagnostic Agent",
        "version":   "1.0.0",
        "status":    "online",
        "ws_clients": manager.client_count,
        "timestamp": time.time(),
    }


@app.get("/health")
async def health():
    return {"ok": True, "timestamp": time.time()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host    = "0.0.0.0",
        port    = 8000,
        reload  = False,
        workers = 1,    # single worker for shared async state
    )