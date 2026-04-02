import React, { useState } from 'react';
import { useWebSocket } from '../hooks/useWebSocket';
import AlertBanner from '../components/AlertBanner';
import SensorGrid from '../components/SensorGrid';
import AgentLog from '../components/AgentLog';
import IncidentHistory from '../components/IncidentHistory';

export default function Dashboard() {
  const { data, isConnected } = useWebSocket('ws://localhost:8000/ws');

  const initiateFault = async (idx) => {
    await fetch('/api/inject-fault', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scenario_index: idx })
    });
  };

  return (
    <div className="dashboard-container">
      <div className="header">
        <h1 style={{margin: '0 0 0.5rem 0'}}>Autonomous Diagnostic Dashboard</h1>
        <p style={{margin: 0, color: '#6b7280'}}>Real-time monitoring and agent interaction</p>
      </div>

      <AlertBanner activeFaults={data.faults} isConnected={isConnected} />

      <div style={{ marginBottom: '1.5rem', display: 'flex', gap: '0.5rem' }}>
        <button onClick={() => initiateFault(0)} style={{ padding: '0.5rem', cursor: 'pointer' }}>Inject Thermal fault</button>
        <button onClick={() => initiateFault(1)} style={{ padding: '0.5rem', cursor: 'pointer' }}>Inject Vibration fault</button>
      </div>

      <div className="grid-layout">
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
          <SensorGrid data={data} />
          <IncidentHistory />
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
          <AgentLog />
        </div>
      </div>
    </div>
  );
}
