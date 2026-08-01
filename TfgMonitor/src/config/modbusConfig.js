export const MODBUS_FORCE_CONFIG = {
  baudRates: ['115200', '921600', '2000000'],
  defaultBaudRate: '115200',
  forceLimitN: 300,
  channels: {
    left: {
      label: '左手指',
      unitId: 1,
      startAddress: 0x0010,
      registerCount: 3,
      registers: {
        fx: 0x0010,
        fy: 0x0011,
        fz: 0x0012
      }
    },
    right: {
      label: '右手指',
      unitId: 2,
      startAddress: 0x0010,
      registerCount: 3,
      registers: {
        fx: 0x0010,
        fy: 0x0011,
        fz: 0x0012
      }
    }
  },
  valueType: 'int16',
  scale: 0.01,
  unit: 'N',
  pollIntervalMs: 20
};
