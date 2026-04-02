"""
db/database.py — Async SQLite database layer with aiosqlite.
Handles schema creation, migrations, and provides a connection factory.
"""
import aiosqlite
import asyncio
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATABASE_PATH = Path("./data/sensors.db")


async def init_db() -> None:
    """Initialize database schema on startup."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        # Sensor readings time-series table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sensor_readings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                sensor_id   TEXT    NOT NULL,
                sensor_type TEXT    NOT NULL,
                value       REAL    NOT NULL,
                unit        TEXT    NOT NULL,
                timestamp   REAL    NOT NULL,
                is_fault    INTEGER DEFAULT 0,
                fault_type  TEXT
            )
        """)
        
        # Index for fast time-range queries
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_sensor_timestamp
            ON sensor_readings(sensor_id, timestamp)
        """)

        # Incidents table — one row per detected anomaly event
        await db.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id     TEXT    NOT NULL UNIQUE,
                sensor_id       TEXT    NOT NULL,
                fault_type      TEXT    NOT NULL,
                severity        TEXT    NOT NULL,
                status          TEXT    DEFAULT 'open',
                detected_at     REAL    NOT NULL,
                resolved_at     REAL,
                anomaly_report  TEXT,
                diagnosis       TEXT,
                corrective_action TEXT,
                report          TEXT
            )
        """)

        # Agent reasoning steps — full audit trail
        await db.execute("""
            CREATE TABLE IF NOT EXISTS agent_steps (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT    NOT NULL,
                step_type   TEXT    NOT NULL,
                tool_name   TEXT,
                tool_input  TEXT,
                tool_output TEXT,
                reasoning   TEXT,
                timestamp   REAL    NOT NULL
            )
        """)

        # Corrective actions log
        await db.execute("""
            CREATE TABLE IF NOT EXISTS corrective_actions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT    NOT NULL,
                action_type TEXT    NOT NULL,
                action_data TEXT,
                executed_at REAL    NOT NULL,
                success     INTEGER DEFAULT 1
            )
        """)

        await db.commit()
    
    logger.info("Database initialized at %s", DATABASE_PATH)


def get_db_path() -> str:
    return str(DATABASE_PATH)