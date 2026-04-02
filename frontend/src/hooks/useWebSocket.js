import { useState, useEffect, useRef } from 'react';

export function useWebSocket(url) {
  const [data, setData] = useState({ state: {}, timestamp: 0, faults: {} });
  const [isConnected, setIsConnected] = useState(false);
  const ws = useRef(null);

  useEffect(() => {
    ws.current = new WebSocket(url);

    ws.current.onopen = () => {
      setIsConnected(true);
      console.log('WS Connected');
    };

    ws.current.onclose = () => {
      setIsConnected(false);
      console.log('WS Disconnected');
    };

    ws.current.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.message_type === 'sensor_update') {
           setData({ state: msg.data.state, faults: msg.data.faults, timestamp: msg.data.timestamp });
        }
      } catch (err) {
        console.error('Error parsing WS message', err);
      }
    };

    return () => {
      if (ws.current) {
        ws.current.close();
      }
    };
  }, [url]);

  return { data, isConnected };
}
