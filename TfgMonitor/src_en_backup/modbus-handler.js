const { SerialPort } = require('serialport');

// Mirror of modbusConfig.js (CommonJS side — keep in sync if config changes)
const CONFIG = {
  channels: {
    left:  { unitId: 1, startAddress: 0x0010, registerCount: 3 },
    right: { unitId: 2, startAddress: 0x0010, registerCount: 3 }
  },
  scale: 0.01,
  pollIntervalMs: 20
};

// Calibration curves per sensor ID: y = a·x³ + b·x² + c·x
// x = sensor raw value (after ×0.01 scale, in N), y = corrected true force (N)
// { a, b, c } — measured 2026-06-08
const CALIBRATION = {
  1: { a:  8e-5,  b: -0.0002, c: 1.0995 },  // 左1
  2: { a:  7e-6,  b:  0.0067, c: 0.8615 },  // 右1
  3: { a: -8e-5,  b:  0.0212, c: 0.777  },  // 左2
  4: { a:  3e-5,  b:  0.0017, c: 0.9147 },  // 右2
};

// Apply cubic calibration; falls back to identity if ID not in table
function applyCalibration(v, unitId) {
  const cal = CALIBRATION[unitId];
  if (!cal) return v;
  return cal.a * v * v * v + cal.b * v * v + cal.c * v;
}

let serialPort = null;
let mainWindow = null;
let polling = false;
let pollTimer = null;

// Modbus RTU CRC16
function crc16(buf) {
  let crc = 0xFFFF;
  for (const byte of buf) {
    crc ^= byte;
    for (let i = 0; i < 8; i++) {
      crc = (crc & 0x0001) ? (crc >> 1) ^ 0xA001 : crc >> 1;
    }
  }
  return crc;
}

// Build FC04 Read Input Registers request (8 bytes)
function buildReadRequest(unitId, startAddr, count) {
  const pdu = Buffer.from([
    unitId, 0x04,
    (startAddr >> 8) & 0xFF, startAddr & 0xFF,
    (count  >> 8) & 0xFF,    count  & 0xFF
  ]);
  const crc = crc16(pdu);
  return Buffer.concat([pdu, Buffer.from([crc & 0xFF, (crc >> 8) & 0xFF])]);
}

// Build FC06 Write Single Register request (8 bytes)
function buildWriteRequest(unitId, addr, value) {
  const pdu = Buffer.from([
    unitId, 0x06,
    (addr  >> 8) & 0xFF, addr  & 0xFF,
    (value >> 8) & 0xFF, value & 0xFF
  ]);
  const crc = crc16(pdu);
  return Buffer.concat([pdu, Buffer.from([crc & 0xFF, (crc >> 8) & 0xFF])]);
}

// Parse FC04 response → array of signed int16, or null on error
function parseReadResponse(buf, unitId, count) {
  const len = 5 + count * 2;
  if (buf.length < len) return null;
  const frame = buf.slice(0, len);
  if (frame[0] !== unitId || frame[1] !== 0x04 || frame[2] !== count * 2) return null;
  const crcCalc = crc16(frame.slice(0, -2));
  const crcMsg  = frame[len - 2] | (frame[len - 1] << 8);
  if (crcCalc !== crcMsg) return null;
  const values = [];
  for (let i = 0; i < count; i++) {
    const raw = (frame[3 + i * 2] << 8) | frame[4 + i * 2];
    values.push(raw > 0x7FFF ? raw - 0x10000 : raw);
  }
  return values;
}

// Parse FC06 echo response → true, or null on error
function parseWriteResponse(buf, unitId, addr, value) {
  if (buf.length < 8) return null;
  const frame = buf.slice(0, 8);
  if (frame[0] !== unitId || frame[1] !== 0x06) return null;
  if (frame[2] !== ((addr  >> 8) & 0xFF) || frame[3] !== (addr  & 0xFF)) return null;
  if (frame[4] !== ((value >> 8) & 0xFF) || frame[5] !== (value & 0xFF)) return null;
  const crcCalc = crc16(frame.slice(0, 6));
  const crcMsg  = frame[6] | (frame[7] << 8);
  if (crcCalc !== crcMsg) return null;
  return true;
}

// Shared pending-transaction state (Modbus RTU is half-duplex: one in-flight at a time)
let rxBuf          = Buffer.alloc(0);
let pendingResolve = null;
let pendingReject  = null;
let pendingExpLen  = 0;
let pendingParser  = null; // (buf) -> parsed | null

function transact(request, expectedLen, parser, timeoutMs = 200) {
  return new Promise((resolve, reject) => {
    const tid = setTimeout(() => {
      pendingResolve = null;
      pendingReject  = null;
      pendingParser  = null;
      reject(new Error('modbus timeout'));
    }, timeoutMs);

    pendingResolve = (val) => { clearTimeout(tid); resolve(val); };
    pendingReject  = (err) => { clearTimeout(tid); reject(err);  };
    pendingExpLen  = expectedLen;
    pendingParser  = parser;
    rxBuf = Buffer.alloc(0);

    serialPort.write(request);
  });
}

function query(unitId, startAddr, count, timeoutMs = 200) {
  return transact(
    buildReadRequest(unitId, startAddr, count),
    5 + count * 2,
    (buf) => parseReadResponse(buf, unitId, count),
    timeoutMs
  );
}

function writeReg(unitId, addr, value, timeoutMs = 200) {
  return transact(
    buildWriteRequest(unitId, addr, value),
    8,
    (buf) => parseWriteResponse(buf, unitId, addr, value),
    timeoutMs
  );
}

function emit(payload) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('modbus-force-data', payload);
  }
}

async function pollLoop() {
  if (!polling || !serialPort?.isOpen) return;

  const { left, right } = CONFIG.channels;
  const scale = CONFIG.scale;

  try {
    const vals = await query(left.unitId, left.startAddress, left.registerCount, 200);
    if (vals) emit({ side: 'left',
      fx: applyCalibration(vals[0] * scale, left.unitId),
      fy: applyCalibration(vals[1] * scale, left.unitId),
      fz: applyCalibration(vals[2] * scale, left.unitId) });
  } catch (_) { /* timeout — keep going */ }

  if (!polling) return;

  // RS485 inter-frame gap: give bus time to settle before querying next device
  await new Promise(r => setTimeout(r, 5));

  if (!polling) return;

  try {
    const vals = await query(right.unitId, right.startAddress, right.registerCount, 200);
    if (vals) emit({ side: 'right',
      fx: applyCalibration(vals[0] * scale, right.unitId),
      fy: applyCalibration(vals[1] * scale, right.unitId),
      fz: applyCalibration(vals[2] * scale, right.unitId) });
  } catch (_) { /* timeout — keep going */ }

  if (polling) {
    pollTimer = setTimeout(pollLoop, CONFIG.pollIntervalMs);
  }
}

function setMainWindow(win) {
  mainWindow = win;
}

async function openPort(portPath, baudRate, leftId = 1, rightId = 2) {
  if (serialPort?.isOpen) await closePort();

  CONFIG.channels.left.unitId  = parseInt(leftId,  10) || 1;
  CONFIG.channels.right.unitId = parseInt(rightId, 10) || 2;

  const baud = parseInt(baudRate, 10);
  if (isNaN(baud)) throw new Error(`invalid baud rate: "${baudRate}"`);

  serialPort = new SerialPort({
    path: portPath, baudRate: baud,
    dataBits: 8, parity: 'none', stopBits: 1,
    autoOpen: false
  });

  return new Promise((resolve, reject) => {
    serialPort.open((err) => {
      if (err) return reject(new Error(`open failed: ${err.message}`));

      rxBuf = Buffer.alloc(0);

      serialPort.on('data', (chunk) => {
        rxBuf = Buffer.concat([rxBuf, chunk]);
        if (pendingResolve && pendingParser && rxBuf.length >= pendingExpLen) {
          const result = pendingParser(rxBuf.slice(0, pendingExpLen));
          const res = pendingResolve;
          const rej = pendingReject;
          pendingResolve = null;
          pendingReject  = null;
          pendingParser  = null;
          if (result !== null) res(result);
          else                 rej(new Error('modbus CRC/format error'));
        }
      });

      serialPort.on('error', (e) => {
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send('modbus-error', e.message);
        }
      });

      polling = true;
      pollLoop();
      resolve({ success: true, message: '串口已打开，开始Modbus轮询' });
    });
  });
}

async function closePort() {
  polling = false;
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  if (pendingReject) {
    pendingReject(new Error('port closing'));
    pendingResolve = null;
    pendingReject  = null;
    pendingParser  = null;
  }
  if (serialPort?.isOpen) {
    await new Promise((resolve) => serialPort.close(resolve));
  }
  serialPort = null;
  return { success: true, message: '串口已关闭' };
}

function isPortOpen() {
  return !!serialPort?.isOpen;
}

// PDF §4.3: hardware zero calibration — write 0xA55A to holding register 0x0007 on each device
async function zeroCal() {
  if (!serialPort?.isOpen) throw new Error('port not open');

  const wasPolling = polling;
  polling = false;
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }

  // Wait for any in-flight poll query to finish (responses arrive within ~2ms at 115200 baud;
  // 10ms gives ample margin before we take over the bus)
  await new Promise(r => setTimeout(r, 10));

  // Flush any stale pending state and receive buffer
  rxBuf          = Buffer.alloc(0);
  pendingResolve = null;
  pendingReject  = null;
  pendingParser  = null;

  const { left, right } = CONFIG.channels;
  const results = { left: false, right: false };

  try { await writeReg(left.unitId,  0x0007, 0xA55A); results.left  = true; } catch (_) {}
  try { await writeReg(right.unitId, 0x0007, 0xA55A); results.right = true; } catch (_) {}

  if (wasPolling) {
    polling = true;
    pollLoop();
  }
  return results;
}

module.exports = { setMainWindow, openPort, closePort, isPortOpen, zeroCal };
