import React, { useEffect, useState } from 'react';

export default function AgentLog() {
  const [incidents, setIncidents] = useState([]);

  useEffect(() => {
    const fetchActive = () => {
      fetch('/api/incidents?status=investigating')
        .then(res => res.json())
        .then(data => {
          if(data.incidents) setIncidents(data.incidents);
        })
        .catch(console.error);
    };
    fetchActive();
    const timer = setInterval(fetchActive, 2000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="card">
      <h2 style={{marginTop: 0}}>Agent Reasoning Log</h2>
      {incidents.length === 0 ? (
        <p style={{color: '#6b7280'}}>Agent is idle. No active incidents.</p>
      ) : (
        <ul style={{ listStyle: 'none', padding: 0 }}>
          {incidents.map(inc => (
            <li key={inc.incident_id} style={{ marginBottom: '1rem', padding: '1rem', background: '#f9fafb', borderRadius: '4px' }}>
              <strong>Incident #{inc.incident_id.slice(0, 8)}</strong>
              <p style={{margin: '0.25rem 0'}}>{inc.agent_summary || 'Investigating...'}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
