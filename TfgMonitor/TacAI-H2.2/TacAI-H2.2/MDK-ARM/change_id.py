from pymodbus.client import ModbusSerialClient

client = ModbusSerialClient(
    port="COM6",
    baudrate=115200,
    bytesize=8,
    parity="N",
    stopbits=1,
    timeout=1,
)

client.connect()

# 当前 slave=1，写 slave_id = 3
client.write_register(address=0x0000, value=3, device_id=1)

# 写保存命令
client.write_register(address=0x0008, value=0x5AA5, device_id=3)

client.close()

y = 8E-05x3 - 0.0002x2 + 1.0995x
y = 7E-06x3 + 0.0067x2 + 0.8615x
y = -8E-05x3 + 0.0212x2 + 0.777x
y = 2E-06x3 + 0.0072x2 + 0.7975x

