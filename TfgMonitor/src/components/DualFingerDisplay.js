import React from 'react';

function getMagnitude(force) {
  return Math.sqrt(force.fx * force.fx + force.fy * force.fy + force.fz * force.fz);
}

const fmt = v => (Math.abs(v) < 0.1 ? 0 : v).toFixed(1);

function getForceColor(force, limit, accent) {
  const ratio = Math.min(getMagnitude(force) / limit, 1);
  if (accent === 'gold') {
    return `hsl(${46 - ratio * 16}, 100%, ${62 - ratio * 8}%)`;
  }
  return `hsl(${188 - ratio * 20}, 95%, ${62 - ratio * 8}%)`;
}

// Piecewise scale: 0-60N linear (sensitive), 60-300N compressed.
// 40N → 88px, 60N → 132px (breakpoint), 300N → 248px (hard-capped to viewBox).
function scaleForce(f, limit) {
  const v = Math.min(Math.max(f, 0), limit);
  const knee = 60;
  const lenKnee = 132;
  const lenMax = 248;
  return v <= knee
    ? (v / knee) * lenKnee
    : lenKnee + ((v - knee) / (limit - knee)) * (lenMax - lenKnee);
}

function ForceArrow({ force, limit, accent }) {
  const color = getForceColor(force, limit, accent);
  const baseX = 130;
  const baseY = 188;

  // Arrow length = total magnitude (responds to Fz pressing too)
  // Arrow direction = Fx/Fy lateral; defaults to pointing up when no lateral force
  const mag = Math.sqrt(force.fx * force.fx + force.fy * force.fy + force.fz * force.fz);
  const lateralMag = Math.sqrt(force.fx * force.fx + force.fy * force.fy);
  const ndx = lateralMag > 0.001 ? force.fx / lateralMag : 0;
  const ndy = lateralMag > 0.001 ? -force.fy / lateralMag : -1;

  const naturalLen = scaleForce(mag, limit);
  const arrowLen = Math.max(0, Math.min(
    naturalLen,
    ndx > 0.001  ? (248 - baseX) / ndx   : naturalLen,
    ndx < -0.001 ? (baseX - 12)  / (-ndx) : naturalLen,
    ndy > 0.001  ? (248 - baseY) / ndy    : naturalLen,
    ndy < -0.001 ? (baseY + 63)  / (-ndy) : naturalLen  // up: tip stays above y=-65
  ));

  const tipX = baseX + ndx * arrowLen;
  const tipY = baseY + ndy * arrowLen;

  // Beam = Fz — capped so top stays within viewBox (y >= -65)
  const beamHeight = Math.min(scaleForce(Math.max(force.fz, 0), limit), baseY + 63);
  const beamTop = baseY - beamHeight;

  const markerId = `ah-${accent}`;
  const glowId = `gl-${accent}`;
  const bgId = `bg-${accent}`;
  const vbeamId = `vb-${accent}`;
  const beamGlowId = `bgl-${accent}`;

  return (
    <svg className="force-vector" viewBox="0 -70 260 330" overflow="visible" role="img" aria-label="3D Force Vector">
      <defs>
        <marker id={markerId} markerWidth="11" markerHeight="11" refX="9.5" refY="5.5" orient="auto">
          <path d="M0,0.5 L11,5.5 L0,10.5 L3,5.5 Z" fill={color} fillOpacity="0.96" />
        </marker>
        <filter id={glowId} x="-80%" y="-80%" width="260%" height="260%">
          <feGaussianBlur stdDeviation="6" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        {/* Beam glow: spread restricted horizontally only — no vertical bleed over arrow */}
        <filter id={beamGlowId} x="-55%" y="-1%" width="210%" height="102%">
          <feGaussianBlur stdDeviation="4" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        <radialGradient id={bgId} cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor={color} stopOpacity="0.68" />
          <stop offset="50%" stopColor={color} stopOpacity="0.22" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </radialGradient>
        <linearGradient id={vbeamId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"   stopColor={color} stopOpacity="0.08" />
          <stop offset="50%"  stopColor={color} stopOpacity="0.65" />
          <stop offset="100%" stopColor={color} stopOpacity="1" />
        </linearGradient>
      </defs>

      <circle cx={baseX} cy={baseY} r="72" fill={`url(#${bgId})`} opacity="0.85" />
      <circle cx={baseX} cy={baseY} r="56" fill="none" stroke={color} strokeWidth="0.8" opacity="0.18" />
      <circle cx={baseX} cy={baseY} r="38" fill="none" stroke={color} strokeWidth="0.9" opacity="0.26" />
      <circle cx={baseX} cy={baseY} r="22" fill="none" stroke={color} strokeWidth="1.0" opacity="0.36" />

      {[0, 0.7, 1.4].map((delay, i) => (
        <circle key={i} cx={baseX} cy={baseY} r="10" fill="none" stroke={color}>
          <animate attributeName="r"            from="10" to="84"  dur="2.1s" begin={`${delay}s`} repeatCount="indefinite" />
          <animate attributeName="opacity"      from="0.85" to="0" dur="2.1s" begin={`${delay}s`} repeatCount="indefinite" />
          <animate attributeName="stroke-width" from="2.8" to="0.4" dur="2.1s" begin={`${delay}s`} repeatCount="indefinite" />
        </circle>
      ))}

      {/* Fz light column — height scales with sqrt(Fz), beam glow stays horizontal */}
      {beamHeight > 2 && (
        <>
          <rect x={baseX - 13} y={beamTop} width="26" height={beamHeight}
                fill={`url(#${vbeamId})`} filter={`url(#${beamGlowId})`} opacity="0.80" />
          <rect x={baseX - 3.5} y={beamTop} width="7" height={beamHeight}
                fill={`url(#${vbeamId})`} opacity="0.9" />
          <rect x={baseX - 1} y={beamTop} width="2" height={beamHeight}
                fill={`url(#${vbeamId})`} opacity="1" />
        </>
      )}

      {/* Fx/Fy lateral force arrow — only rendered when lateral force is present */}
      {arrowLen > 4 && (
        <>
          <line x1={baseX} y1={baseY} x2={tipX} y2={tipY}
                stroke={color} strokeWidth="6.5" strokeLinecap="round"
                markerEnd={`url(#${markerId})`} filter={`url(#${glowId})`} opacity="0.93" />
          <line x1={baseX} y1={baseY}
                x2={baseX + ndx * arrowLen * 0.8} y2={baseY + ndy * arrowLen * 0.8}
                stroke="#ffffff" strokeWidth="1.8" strokeLinecap="round" opacity="0.46" />
        </>
      )}

      <circle cx={baseX} cy={baseY} r="14" fill={color} opacity="0.22">
        <animate attributeName="r"       values="12;20;12" dur="2.1s" repeatCount="indefinite" />
        <animate attributeName="opacity" values="0.22;0.07;0.22" dur="2.1s" repeatCount="indefinite" />
      </circle>

      <circle cx={baseX} cy={baseY} r="8" fill={color} filter={`url(#${glowId})`} opacity="0.96">
        <animate attributeName="opacity" values="0.96;0.72;0.96" dur="2.1s" repeatCount="indefinite" />
      </circle>
    </svg>
  );
}

function AxisTriad({ accent }) {
  const cFx = '#37c8ff';
  const cFy = accent === 'gold' ? '#ffc83d' : '#31d85d';
  const cFz = '#5577ee';

  const Mk = ({ id, fill }) => (
    <marker id={id} markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto">
      <path d="M0,0 L5,2.5 L0,5 Z" fill={fill} />
    </marker>
  );

  return (
    <svg className="axis-triad" viewBox="0 0 132 116" aria-label="Axis">
      <defs>
        <Mk id={`mfx-${accent}`} fill={cFx} />
        <Mk id={`mfy-${accent}`} fill={cFy} />
        <Mk id={`mfz-${accent}`} fill={cFz} />
      </defs>
      {/* Fx: horizontal right */}
      <line x1="34" y1="86" x2="88" y2="86" stroke={cFx} markerEnd={`url(#mfx-${accent})`} />
      {/* Fy: vertical up (Fy+ = up, Fy- = down) */}
      <line x1="34" y1="86" x2="34" y2="28" stroke={cFy} markerEnd={`url(#mfy-${accent})`} />
      {/* Fz: diagonal (depth / normal force, shown by beam) */}
      <line x1="34" y1="86" x2="101" y2="43" stroke={cFz} markerEnd={`url(#mfz-${accent})`} />
      <text x="94" y="96" fill={cFx}>Fx</text>
      <text x="20" y="25" fill={cFy}>Fy</text>
      <text x="105" y="41" fill={cFz}>Fz</text>
    </svg>
  );
}

function FingerCard({ side, title, force, limit }) {
  const magnitude = getMagnitude(force);
  const ratio = Math.min(magnitude / limit, 1);
  const accent = side === 'right' ? 'gold' : 'cyan';
  const color = getForceColor(force, limit, accent);

  const shiftX = `${Math.max(-38, Math.min(38, -force.fx / 2.5))}%`;
  const shiftY = `${Math.max(-38, Math.min(38,  force.fy / 2.5))}%`;
  const glowOpacity = Math.min(0.88, Math.max(0, force.fz / (limit * 0.9)));

  return (
    <article className={`finger-card ${side}`} style={{ '--force-color': color }}>
      <header className="finger-card-title">
        <span className="finger-icon">☝</span>
        <h3>{title}三维力动态示意</h3>
      </header>
      <div className="finger-shell" style={{ '--force-color': color, '--press-depth': `${ratio * 18}px` }}>
        <div className="finger-pad" style={{ '--shift-x': shiftX, '--shift-y': shiftY, '--glow-opacity': glowOpacity }}>
          <ForceArrow force={force} limit={limit} accent={accent} />
        </div>
        <AxisTriad accent={accent} />
      </div>

      <div className="finger-readout">
        <div className="readout-magnitude">
          <span className="readout-mag-label">合力</span>
          <strong>{magnitude.toFixed(1)} N</strong>
        </div>
        <div className="force-bars">
          <span className="fx">
            <span className="fbar-label">Fx</span>
            <span className="fbar-value">{fmt(force.fx)} N</span>
          </span>
          <span className="fy">
            <span className="fbar-label">Fy</span>
            <span className="fbar-value">{fmt(force.fy)} N</span>
          </span>
          <span className="fz">
            <span className="fbar-label">Fz</span>
            <span className="fbar-value">{fmt(force.fz)} N</span>
          </span>
          <span className={ratio > 0.92 ? 'status warn' : 'status'}>
            <span className="fbar-label">状态</span>
            <span className="fbar-value">{ratio > 0.92 ? '超载' : '正常'}</span>
          </span>
        </div>
      </div>
    </article>
  );
}

function DualFingerDisplay({ forces, limit }) {
  return (
    <div className="dual-finger-display">
      <div className="finger-pair">
        <FingerCard side="left"  title="左手指"  force={forces.left}  limit={limit} />
        <FingerCard side="right" title="右手指" force={forces.right} limit={limit} />
      </div>
    </div>
  );
}

export default DualFingerDisplay;
