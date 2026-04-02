import React, { useEffect, useState } from 'react';

export default function IncidentHistory() {
  const [incidents, setIncidents] = useState([]);

  useEffect(() => {
    const fetchHistory = () => {
      fetch('/api/incidents?limit=5')
        .then(res => res.json())
        .then(data => {
          if(data.incidents) setIncidents(data.incidents);
        })
        .catch(console.error);
    };
    fetchHistory();
    const timer = setInterval(fetchHistory, 2000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="card">
      <h2 style={{marginTop: 0}}>Recent Incidents</h2>
      {incidents.length === 0 ? (
        <p style={{color: '#6b7280'}}>No historical incidents found.</p>
      ) : (
        <ul style={{ paddingLeft: '1.25rem', margin: 0 }}>
          {incidents.map(inc => (
            <li key={inc.incident_id} style={{ marginBottom: '0.5rem' }}>
              <strong>{inc.fault_type}</strong> - {new Date(inc.detected_at * 1000).toLocaleString()}<br/>
              Status: <span style={{ color: inc.status === 'resolved' ? '#166534' : '#92400e' }}>{inc.status}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
