import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import DualSerialConfig from './components/DualSerialConfig';
import DualFingerDisplay from './components/DualFingerDisplay';
import ForceDashboard from './components/ForceDashboard';
import './styles/App.css';

const emptyForce = { fx: 0, fy: 0, fz: 0, timestamp: Date.now() };
const emptyGrid = Array(6).fill(null).map(() => ({ x: 0, y: 0, z: 0 }));

// 根据三维力生成 6 点高斯压力分布（3行×2列）
// 点序：data[0]=底右, [1]=底左, [2]=顶右, [3]=顶左, [4]=中右, [5]=中左
function makeGrid(fx, fy, fz, t, seed) {
  const positions = [
    { dy: 1, dx: 0.5 },
    { dy: 1, dx: -0.5 },
    { dy: -1, dx: 0.5 },
    { dy: -1, dx: -0.5 },
    { dy: 0, dx: 0.5 },
    { dy: 0, dx: -0.5 },
  ];
  const base = Math.max(0, fz);
  const cx = fx / 160;
  const cy = -fy / 160;
  const sigma2 = 0.75;
  return positions.map(({ dy, dx }, i) => {
    const dist2 = (dy - cy) ** 2 + (dx - cx) ** 2;
    const pressure = base * Math.exp(-dist2 / sigma2);
    const jitter = Math.sin(t * 1.9 + i * 2.3 + seed) * 2.5;
    return { x: 0, y: 0, z: Math.max(0, pressure + jitter) };
  });
}

function subtractOffset(force, offset) {
  return {
    fx: force.fx - offset.fx,
    fy: force.fy - offset.fy,
    fz: force.fz - offset.fz,
    timestamp: force.timestamp
  };
}

function App() {
  const [rawForces, setRawForces] = useState({
    left: emptyForce,
    right: emptyForce
  });
  const [offsets, setOffsets] = useState({
    left: emptyForce,
    right: emptyForce
  });
  const [grids, setGrids] = useState({ left: emptyGrid, right: emptyGrid });
  const [demoMode, setDemoMode] = useState(true);
  const [clearCount, setClearCount] = useState(0);
  const demoStart = useRef(Date.now());
  const rawForcesRef = useRef(rawForces);

  useEffect(() => { rawForcesRef.current = rawForces; }, [rawForces]);

  const forces = useMemo(() => ({
    left: subtractOffset(rawForces.left, offsets.left),
    right: subtractOffset(rawForces.right, offsets.right)
  }), [rawForces, offsets]);

  const handleCalibrate = useCallback(async () => {
    if (window.electron?.ipcRenderer) {
      try {
        await window.electron.ipcRenderer.invoke('modbus-calibrate');
      } catch (_) {}
    }
    // Wait for sensor to settle after hardware calibration, then capture current reading
    await new Promise(r => setTimeout(r, 1500));
    setOffsets({
      left:  { ...rawForcesRef.current.left  },
      right: { ...rawForcesRef.current.right }
    });
  }, []);

  const handleClear = useCallback(() => {
    setClearCount((c) => c + 1);
  }, []);

  useEffect(() => {
    if (!demoMode) return undefined;

    const timer = setInterval(() => {
      const t = (Date.now() - demoStart.current) / 1000;
      const lf = {
        fx: 52 * Math.sin(t * 1.4),
        fy: 38 * Math.cos(t * 0.9),
        fz: 95 + 85 * (0.5 + 0.5 * Math.sin(t * 1.1)),
        timestamp: Date.now()
      };
      const rf = {
        fx: 46 * Math.cos(t * 1.1 + 0.8),
        fy: 42 * Math.sin(t * 1.5 + 1.2),
        fz: 80 + 110 * (0.5 + 0.5 * Math.cos(t * 0.95)),
        timestamp: Date.now()
      };
      setRawForces({ left: lf, right: rf });
      setGrids({
        left: makeGrid(lf.fx, lf.fy, lf.fz, t, 0),
        right: makeGrid(rf.fx, rf.fy, rf.fz, t, 3.7)
      });
    }, 80);

    return () => clearInterval(timer);
  }, [demoMode]);

  useEffect(() => {
    if (!window.electron?.ipcRenderer) return undefined;

    const handleModbusForceData = (event, payload) => {
      if (!payload || !payload.side) return;
      setRawForces((prev) => ({
        ...prev,
        [payload.side]: {
          fx: Number(payload.fx) || 0,
          fy: Number(payload.fy) || 0,
          fz: Number(payload.fz) || 0,
          timestamp: Date.now()
        }
      }));
    };

    const handleLegacySerialData = (event, values) => {
      if (!Array.isArray(values) || values.length < 6) return;
      setRawForces({
        left: {
          fx: Number(values[0]) || 0,
          fy: Number(values[1]) || 0,
          fz: Number(values[2]) || 0,
          timestamp: Date.now()
        },
        right: {
          fx: Number(values[3]) || 0,
          fy: Number(values[4]) || 0,
          fz: Number(values[5]) || 0,
          timestamp: Date.now()
        }
      });
    };

    window.electron.ipcRenderer.on('modbus-force-data', handleModbusForceData);
    window.electron.ipcRenderer.on('serial-data', handleLegacySerialData);

    return () => {
      window.electron.ipcRenderer.removeAllListeners('modbus-force-data');
      window.electron.ipcRenderer.removeAllListeners('serial-data');
    };
  }, []);

  return (
    <div className="app">
      {demoMode && (
        <button type="button" className="demo-badge" onClick={() => setDemoMode(false)}>
          <span className="status-dot" />模拟运行中
        </button>
      )}

      <header className="app-header">
        <div>
          <p className="app-kicker">300N 双指单点三维力演示</p>
          <h1>触觉传感器三维力可视化平台</h1>
        </div>
        <DualSerialConfig
          demoMode={demoMode}
          onDemoModeChange={setDemoMode}
          onCalibrate={handleCalibrate}
          onClear={handleClear}
        />
      </header>

      <main className="app-content">
        <section className="finger-stage">
          <DualFingerDisplay forces={forces} limit={300} />
        </section>

        <aside className="dashboard-stage">
          <ForceDashboard forces={forces} limit={300} demoMode={demoMode} clearCount={clearCount} />
        </aside>
      </main>

      <footer className="app-footer">
        <img src="./gliix-log.png" alt="GLiiX" className="footer-logo" />
      </footer>
    </div>
  );
}

export default App;
