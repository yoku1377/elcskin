const { SerialPort } = require('serialport');
const { DataParser } = require('./data-parser');

const frame_length = 18;
let serialPort = null;
let dataParser = new DataParser();
let mainWindow = null;
let baselineCalculated = false;
let baseline = [];
let lastData = null; // 添加变量来保存最后一次接收的数据

function setMainWindow(window) {
    mainWindow = window;
}

function emit(event, payload) {
    if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send(event, payload);
    }
}

async function openPort(portPath, baudRate) {
    if (serialPort && serialPort.isOpen) {
        await closePort();
    }

    const baudRateNum = parseInt(baudRate, 10);
    if (isNaN(baudRateNum)) {
        throw new Error(`invalid baud rate: "${baudRate}"`);
    }

    serialPort = new SerialPort({
        path: portPath,
        baudRate: baudRateNum,
        autoOpen: false
    });

    return new Promise((resolve, reject) => {
        serialPort.open((err) => {
            if (err) {
                return reject(new Error(`open failed: ${err.message}`));
            }

            // 清空上次解析状态和基线
            dataParser.clear();
            baselineCalculated = false;
            baseline = [];
            lastData = null;

            // 数据监听
            serialPort.on('data', (data) => {

                const text = data.toString();
                const parsed = dataParser.parse(text);

                if (parsed) {
                    // 保存最后一次接收的数据
                    lastData = parsed;
                    // 处理数据：检查长度并应用基线校正
                    if (parsed.length === frame_length) {
                        if (!baselineCalculated) {
                            // 计算基线
                            baseline = [...parsed];
                            baselineCalculated = true;

                            // 发送全零数据（基线减去自身）
                            const zeroData = new Array(frame_length).fill(0);
                            emit('serial-data', zeroData);
                        } else {
                            // 减除基线
                            const processedData = parsed.map((value, index) =>
                                value - baseline[index]
                            );
                            emit('serial-data', processedData);
                        }
                    } else {
                        // 长度不为frame_length的数据直接发送
                        emit('serial-data', parsed);
                    }
                }
            });

            serialPort.on('error', (err) => {
                console.error('Serial error:', err);
                emit('serial-error', err.message);
            });

            resolve({ success: true, message: '串口已打开' });
        });
    });
}

async function closePort() {
    if (serialPort && serialPort.isOpen) {
        await serialPort.close();
        serialPort = null;
    }
    return { success: true, message: '串口已关闭' };
}

async function calibration() {
    // 检查是否有最新数据可用
    if (!lastData || lastData.length !== frame_length) {
        throw new Error('no frame_length channels for calibration');
    }

    // 使用最新接收的数据作为新基线
    baseline = [...lastData];
    baselineCalculated = true;

    console.log('基线已更新:', baseline);

    // 发送全零数据（表示当前数据已减去新基线）
    const zeroData = new Array(frame_length).fill(0);
    emit('serial-data', zeroData);

    return { success: true, message: '基线已更新' };
}

function isPortOpen() {
    return !!serialPort && serialPort.isOpen;
}

module.exports = {
    setMainWindow,
    openPort,
    closePort,
    calibration,
    isPortOpen
};