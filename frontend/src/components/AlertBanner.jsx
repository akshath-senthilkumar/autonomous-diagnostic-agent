import React from 'react';
import { AlertTriangle, CheckCircle, Info } from 'lucide-react';

export default function AlertBanner({ activeFaults = {}, isConnected }) {
  const faults = Object.keys(activeFaults);

  if (!isConnected) {
    return (
      <div className="alert-banner" style={{ backgroundColor: '#fef3c7', borderLeftColor: '#f59e0b', color: '#92400e' }}>
         <Info size={18} style={{ display: 'inline', marginRight: '8px', verticalAlign: 'middle' }} />
         Disconnected from server. Attempting to reconnect...
      </div>
    );
  }

  if (faults.length === 0) {
    return (
      <div className="alert-banner" style={{ backgroundColor: '#dcfce7', borderLeftColor: '#22c55e', color: '#166534' }}>
        <CheckCircle size={18} style={{ display: 'inline', marginRight: '8px', verticalAlign: 'middle' }} />
        System normal. All sensors operating within parameters.
      </div>
    );
  }

  return (
    <div className="alert-banner">
      <AlertTriangle size={18} style={{ display: 'inline', marginRight: '8px', verticalAlign: 'middle' }} />
      <strong>CRITICAL ALERT: </strong> Fault detected in sensors: {faults.join(', ')}
    </div>
  );
}
