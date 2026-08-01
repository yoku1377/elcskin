import React, { useEffect, useMemo, useState } from 'react';
import { MODBUS_FORCE_CONFIG } from '../config/modbusConfig';

function DualSerialConfig({ demoMode, onDemoModeChange, onCalibrate, onClear }) {
  const [ports, setPorts] = useState([]);
  const [port, setPort] = useState('');
  const [baudRate, setBaudRate] = useState(MODBUS_FORCE_CONFIG.defaultBaudRate);
  const [isLoading, setIsLoading] = useState(false);
  const [connected, setConnected] = useState(false);
  const [statusMsg, setStatusMsg] = useState('');
  const [leftId, setLeftId] = useState(String(MODBUS_FORCE_CONFIG.channels.left.unitId));
  const [rightId, setRightId] = useState(String(MODBUS_FORCE_CONFIG.channels.right.unitId));

  const portOptions = useMemo(() => ports.map((p) => ({
    value: p.path,
    label: `${p.path}${p.manufacturer ? ` - ${p.manufacturer}` : ''}`
  })), [ports]);

  const loadPorts = async () => {
    if (!window.electron?.ipcRenderer) return;
    setIsLoading(true);
    try {
      const portList = await window.electron.ipcRenderer.invoke('get-ports');
      setPorts(portList);
      if (!port && portList[0]) setPort(portList[0].path);
    } catch (error) {
      console.error('Failed to load ports:', error);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadPorts();
  }, []);

  const handleConnect = async () => {
    if (!window.electron?.ipcRenderer) return;

    if (connected) {
      try {
        await window.electron.ipcRenderer.invoke('modbus-close');
        setConnected(false);
        setStatusMsg('');
        onDemoModeChange(true);
      } catch (err) {
        setStatusMsg(`Disconnect failed: ${err.message}`);
      }
      return;
    }

    if (!port) { setStatusMsg('Please select a port'); return; }

    try {
      const result = await window.electron.ipcRenderer.invoke('modbus-open', port, baudRate, parseInt(leftId, 10) || 1, parseInt(rightId, 10) || 2);
      if (result?.success) {
        setConnected(true);
        setStatusMsg('');
        onDemoModeChange(false);
      } else {
        setStatusMsg(result?.message || 'Connect failed');
      }
    } catch (err) {
      setStatusMsg(`Connect failed: ${err.message}`);
    }
  };

  return (
    <div className="dual-serial-config">
      <div className="serial-row">
        <label>
          <span>Port</span>
          <select value={port} onChange={(e) => setPort(e.target.value)} disabled={connected}>
            <option value="">Select Port</option>
            {portOptions.map((p) => (
              <option key={p.value} value={p.value}>{p.label}</option>
            ))}
          </select>
        </label>

        <label className="baud-select">
          <span>Baud Rate</span>
          <select value={baudRate} onChange={(e) => setBaudRate(e.target.value)} disabled={connected}>
            {MODBUS_FORCE_CONFIG.baudRates.map((rate) => (
              <option key={rate} value={rate}>{rate} baud</option>
            ))}
          </select>
        </label>
      </div>

      <div className="serial-actions">
        <button type="button" className="refresh-btn" onClick={loadPorts} disabled={isLoading || connected}>
          <span className="btn-icon">↻</span>{isLoading ? 'Loading' : 'Refresh'}
        </button>
        <button type="button" className="clear-btn" onClick={onClear}>
          <span className="btn-icon">⊕</span>Clear
        </button>
        <button type="button" className="calibrate-btn" onClick={onCalibrate} disabled={!connected}>
          <span className="btn-icon">⌾</span>Zero Cal
        </button>
        <button
          type="button"
          className={connected ? 'connect-btn active' : 'connect-btn'}
          onClick={handleConnect}
        >
          <span className="btn-icon">⌁</span>{connected ? 'Disconnect' : 'Connect'}
        </button>
      </div>

      {statusMsg && <div className="modbus-status-error">{statusMsg}</div>}

      <div className="serial-row">
        <label className="id-label">
          <span>Left ID</span>
          <input
            type="text" inputMode="numeric"
            value={leftId}
            onChange={(e) => { if (/^\d*$/.test(e.target.value)) setLeftId(e.target.value); }}
            disabled={connected}
            className="id-input"
          />
        </label>
        <label className="id-label">
          <span>Right ID</span>
          <input
            type="text" inputMode="numeric"
            value={rightId}
            onChange={(e) => { if (/^\d*$/.test(e.target.value)) setRightId(e.target.value); }}
            disabled={connected}
            className="id-input"
          />
        </label>
      </div>
    </div>
  );
}

export default DualSerialConfig;
