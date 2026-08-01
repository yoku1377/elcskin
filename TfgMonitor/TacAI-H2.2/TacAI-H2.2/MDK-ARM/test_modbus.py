from pymodbus.client import ModbusSerialClient
import time

PORT = "COM4"
BAUD = 115200
SLAVE_ID = 3

client = ModbusSerialClient(
    port=PORT,
    baudrate=BAUD,
    bytesize=8,
    parity="N",
    stopbits=1,
    timeout=1,
)

if not client.connect():
    print("Failed to open serial port")
    raise SystemExit(1)

# 1. 读一个 input register，功能码 0x04
rr = client.read_input_registers(
    address=0x0001,   # fw_version
    count=1,
    device_id=SLAVE_ID,
)

if rr.isError():
    print("Read input register failed:", rr)
else:
    print("fw_version =", rr.registers[0], hex(rr.registers[0]))

# 2. 写一个 holding register，功能码 0x06
wr = client.write_register(
    address=0x0005,   # filter_level
    value=3,
    device_id=SLAVE_ID,
)

if wr.isError():
    print("Write holding register failed:", wr)
else:
    print("write filter_level = 3 ok")

# 3. 读回来，功能码 0x03
rr = client.read_holding_registers(
    address=0x0005,
    count=1,
    device_id=SLAVE_ID,
)


# 2. 写一个 holding register，功能码 0x06
wr = client.write_register(
    address=0x0005,   # filter_level
    value=2,
    device_id=SLAVE_ID,
)

if wr.isError():
    print("Write holding register failed:", wr)
else:
    print("write filter_level = 2 ok")

# 3. 读回来，功能码 0x03
rr = client.read_holding_registers(
    address=0x0005,
    count=1,
    device_id=SLAVE_ID,
)


if rr.isError():
    print("Read holding register failed:", rr)
else:
    print("filter_level =", rr.registers[0])

# 向 0x0007 写 0xA55A 触发基线校准
wr = client.write_register(
    address=0x0007,   # baseline calibration trigger
    value=0xA55A,
    device_id=SLAVE_ID,
)

if wr.isError():
    print("Write baseline calibration trigger failed:", wr)
else:
    print("baseline calibration trigger written: 0xA55A")

while(1):
    rr = client.read_input_registers(
        address=0x0010,
        count=3,
        device_id=SLAVE_ID,
    )
    raw_force = [r if r < 0x8000 else r - 0x10000 for r in rr.registers]
    print("raw force =", raw_force[0], raw_force[1], raw_force[2])
    time.sleep(0.01)
client.close()