# 🤖 Autonomous Embedded Systems Diagnostic Agent

An agentic AI system that monitors real-time IoT/embedded sensor data streams, autonomously diagnoses faults, and triggers corrective actions using LLM tool-calling — no human intervention required.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 4: React.js Live Dashboard + WebSocket Streaming     │
├─────────────────────────────────────────────────────────────┤
│  Layer 3: Agentic AI Core (Claude API + Tool Calling)       │
│   ├── query_sensor_history()   ├── detect_anomaly()         │
│   ├── diagnose_fault()         ├── trigger_corrective_action│
│   └── generate_report()                                     │
├─────────────────────────────────────────────────────────────┤
│  Layer 2: FastAPI Backend + WebSockets + SQLite             │
├─────────────────────────────────────────────────────────────┤
│  Layer 1: Sensor Simulator (Temp, Vibration, Voltage, Curr) │
└─────────────────────────────────────────────────────────────┘
```

## Tech Stack

- **Backend**: Python 3.11, FastAPI, WebSockets, SQLite, asyncio
- **Agent**: Claude API (claude-sonnet-4) with autonomous tool-calling
- **Frontend**: React.js, Recharts, WebSocket client
- **DevOps**: Docker, GitHub Actions, Render/Railway

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+
- Anthropic API key

### Backend Setup

```bash
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env      # Add your ANTHROPIC_API_KEY
python main.py
```

### Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

### Docker (Full Stack)

```bash
docker-compose up --build
```

## Project Structure

```
embedded-diag-agent/
├── backend/
│   ├── main.py                 # FastAPI app entry point
│   ├── requirements.txt
│   ├── .env.example
│   ├── agent/
│   │   ├── tools.py            # All LLM-callable tools
│   │   ├── agent_core.py       # Autonomous agent loop
│   │   └── prompts.py          # System prompts
│   ├── api/
│   │   ├── routes.py           # REST endpoints
│   │   └── websocket.py        # WebSocket manager
│   ├── db/
│   │   ├── database.py         # SQLite setup
│   │   └── models.py           # Data models
│   ├── sensors/
│   │   └── simulator.py        # IoT sensor simulation
│   └── utils/
│       └── alerts.py           # Alert system
├── frontend/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── components/
│   │   │   ├── SensorGrid.jsx
│   │   │   ├── AgentLog.jsx
│   │   │   ├── IncidentHistory.jsx
│   │   │   └── AlertBanner.jsx
│   │   ├── hooks/
│   │   │   └── useWebSocket.js
│   │   └── pages/
│   │       └── Dashboard.jsx
│   └── package.json
├── tests/
│   ├── test_agent.py
│   ├── test_sensors.py
│   └── test_api.py
├── docker/
│   ├── Dockerfile.backend
│   └── Dockerfile.frontend
├── docker-compose.yml
└── .github/workflows/
    └── ci-cd.yml
```

## Agent Tools

| Tool | Description |
|------|-------------|
| `query_sensor_history` | Fetch historical readings from SQLite by sensor ID and time range |
| `detect_anomaly` | Run Z-score + IQR statistical anomaly detection |
| `diagnose_fault` | LLM-powered root cause analysis from sensor data |
| `trigger_corrective_action` | Execute and log corrective responses |
| `generate_report` | Produce structured incident report |

## Fault Scenarios Simulated

- **Thermal Runaway**: Temperature spike > 85°C
- **Motor Bearing Fault**: Vibration amplitude anomaly
- **Power Supply Instability**: Voltage dropout / ripple
- **Overcurrent Event**: Current exceeds safe operating threshold
- **Sensor Dropout**: NaN injection / flatline detection

## Resume Points

- Autonomous multi-step LLM agent with tool orchestration (no human-in-loop)
- Real-time WebSocket streaming pipeline with SQLite time-series logging
- Statistical anomaly detection (Z-score + IQR) on embedded sensor streams
- Production FastAPI service with async background tasks
- Full CI/CD with Docker + GitHub Actions + cloud deployment