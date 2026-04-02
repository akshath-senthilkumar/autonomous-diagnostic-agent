import React from 'react';

export default function SensorGrid({ data = {} }) {
  const sensors = Object.keys(data.state || {});

  return (
    <div className="card">
      <h2 style={{marginTop: 0}}>Real-Time Sensor Values</h2>
      <div className="grid-layout">
        {sensors.map((sensor) => {
          const isFault = data.faults[sensor];
          return (
            <div key={sensor} style={{
              padding: '1rem',
              border: `2px solid ${isFault ? '#ef4444' : '#e5e7eb'}`,
              borderRadius: '8px',
              backgroundColor: isFault ? '#fee2e2' : 'white'
            }}>
              <h3 style={{ margin: '0 0 0.5rem 0', textTransform: 'capitalize' }}>{sensor.replace('_', ' ')}</h3>
              <p style={{ margin: 0, fontSize: '1.5rem', fontWeight: 'bold', color: isFault ? '#b91c1c' : '#1f2937' }}>
                {data.state[sensor].toFixed(2)}
              </p>
            </div>
          );
        })}
        {sensors.length === 0 && <p>Waiting for sensor data...</p>}
      </div>
    </div>
  );
}
