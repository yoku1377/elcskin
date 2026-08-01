import React, { useEffect, useMemo, useState } from 'react';
import { MODBUS_FORCE_CONFIG } from '../config/modbusConfig';

function magnitude(force) {
  return Math.sqrt(force.fx * force.fx + force.fy * force.fy + force.fz * force.fz);
}

function ForceMeter({ label, force, limit }) {
  const value = magnitude(force);
  const ratio = Math.min(value / limit, 1);
  const status = ratio > 0.92 ? 'Overload' : ratio > 0.72 ? 'High Load' : 'Normal';
  const isRight = label.toLowerCase().includes('right');

  return (
    <div className={isRight ? 'force-meter right' : 'force-meter left'}>
      <div className="meter-header">
        <span>{label}</span>
        <strong>{value.toFixed(1)} N</strong>
      </div>
      <div className="meter-track" style={{ '--meter-ratio': `${ratio * 100}%` }}>
        <div className="meter-fill" style={{ width: `${ratio * 100}%` }} />
      </div>
      <div className="meter-values">
        <span className="fx"><span className="fbar-label">Fx</span>{(Math.abs(force.fx) < 0.1 ? 0 : force.fx).toFixed(1)} N</span>
        <span className="fy"><span className="fbar-label">Fy</span>{(Math.abs(force.fy) < 0.1 ? 0 : force.fy).toFixed(1)} N</span>
        <span className="fz"><span className="fbar-label">Fz</span>{(Math.abs(force.fz) < 0.1 ? 0 : force.fz).toFixed(1)} N</span>
      </div>
      <p className={ratio > 0.92 ? 'meter-status warn' : 'meter-status'}>{status}</p>
    </div>
  );
}

function MiniTrend({ samples, limit }) {
  const svgW = 390;
  const svgH = 140;
  const padL = 36;
  const padR = 6;
  const padT = 14;
  const padB = 22;
  const chartW = svgW - padL - padR;
  const chartH = svgH - padT - padB;
  const max = Math.max(limit, ...samples.map((s) => s.left), ...samples.map((s) => s.right));

  const toY = (v) => padT + chartH - (v / max) * chartH;
  const xOfFrac = (f) => padL + f * chartW;

  const buildPoints = (key) => {
    if (samples.length === 0) return '';
    return samples.map((sample, index) => {
      const x = xOfFrac(samples.length === 1 ? 0 : index / (samples.length - 1));
      const y = toY(sample[key]);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
  };

  const yTicks = [0, 100, 200, 300].filter((v) => v <= limit + 1);
  const xFracs = [0, 1 / 3, 2 / 3, 1];
  const xLabels = ['-30s', '-20s', '-10s', '0'];

  return (
    <svg className="mini-trend" viewBox={`0 0 ${svgW} ${svgH}`} preserveAspectRatio="none">
      {/* 水平格线，精确对齐刻度 */}
      {yTicks.map((v) => (
        <line key={`h${v}`} x1={padL} y1={toY(v)} x2={svgW - padR} y2={toY(v)} className="trend-grid-h" />
      ))}
      {/* 垂直格线，精确对齐时间标签 */}
      {xFracs.map((f, i) => (
        <line key={`v${i}`} x1={xOfFrac(f)} y1={padT} x2={xOfFrac(f)} y2={padT + chartH} className="trend-grid-v" />
      ))}
      {/* Y 轴标签，dominant-baseline 精确垂直居中 */}
      {yTicks.map((v) => (
        <text key={v} x={padL - 5} y={toY(v)} className="trend-axis-label" textAnchor="end" dominantBaseline="central">{v}</text>
      ))}
      {/* X 轴标签 */}
      {xLabels.map((label, i) => (
        <text key={label} x={xOfFrac(xFracs[i])} y={svgH - 5} className="trend-axis-label" textAnchor="middle">{label}</text>
      ))}
      <line x1={padL} y1={toY(limit)} x2={svgW - padR} y2={toY(limit)} className="trend-limit-line" />
      <polyline points={buildPoints('left')} className="trend-left" />
      <polyline points={buildPoints('right')} className="trend-right" />
    </svg>
  );
}

function ForceDashboard({ forces, limit, demoMode, clearCount }) {
  const [samples, setSamples] = useState([]);

  useEffect(() => {
    setSamples([]);
  }, [clearCount]);

  useEffect(() => {
    const point = {
      left: magnitude(forces.left),
      right: magnitude(forces.right)
    };
    setSamples((prev) => [...prev.slice(-59), point]);
  }, [forces]);

  const peak = useMemo(() => samples.reduce((acc, sample) => Math.max(acc, sample.left, sample.right), 0), [samples]);

  return (
    <div className="force-dashboard">
      <div className="dashboard-title">
        <p>Live Data</p>
        <h2>300N Range Monitor</h2>
      </div>

      <div className="status-grid">
        <div>
          <span>Status</span>
          <p className="acquire-status">
            <span className={`acquire-dot ${demoMode ? 'active' : ''}`} />
            {demoMode ? 'Demo' : 'Modbus Active'}
          </p>
        </div>
        <div>
          <span>Peak</span>
          <strong>{peak.toFixed(1)} N</strong>
        </div>
      </div>

      <ForceMeter label="Left Finger" force={forces.left} limit={limit} />
      <ForceMeter label="Right Finger" force={forces.right} limit={limit} />

      <div className="trend-panel">
        <div className="trend-header">
          <span>Force Trend</span>
          <div>
            <i className="legend-left" />L
            <i className="legend-right" />R
          </div>
        </div>
        <MiniTrend samples={samples} limit={limit} />
      </div>

      <div className="protocol-card">
        <h3>Interface Spec</h3>
        <p>One Modbus RTU slave per port, default Slave ID: {MODBUS_FORCE_CONFIG.channels.left.unitId}.</p>
        <p>Input registers 0x0010/0x0011/0x0012 → Fx/Fy/Fz, int16, scale {MODBUS_FORCE_CONFIG.scale} N/count.</p>
        <p>Poll interval {MODBUS_FORCE_CONFIG.pollIntervalMs} ms, display clipped at ±{limit} N.</p>
      </div>
    </div>
  );
}

export default ForceDashboard;
