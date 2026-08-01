const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const { SerialPort } = require('serialport');

// 导入串口模块
const SerialHandler = require('./serial-handler');
const ModbusHandler = require('./modbus-handler');

let mainWindow;

function createWindow() {
    mainWindow = new BrowserWindow({
        width: 1440,
        minWidth: 1180,
        height: 860,
        minHeight: 720,
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            webSecurity: false,
        },
        icon: app.isPackaged
            ? path.join(__dirname, '../build/mevis.ico')
            : path.join(__dirname, '../public/mevis.ico'),
        backgroundColor: '#030c1a',
        autoHideMenuBar: true,
        contextIsolation: false,
        title: 'GLiiX — Tactile Force Monitor',
    });

    // 设置主窗口引用
    SerialHandler.setMainWindow(mainWindow);
    ModbusHandler.setMainWindow(mainWindow);

    // mainWindow.loadURL('http://localhost:3000');
    mainWindow.loadURL(`file://${path.join(__dirname, '../build/index.html')}`);

    // mainWindow.webContents.openDevTools();
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

function sanitizeManufacturer(mfr) {
    if (!mfr) return '';
    const map = [
        ['标准端口类型',       'Standard Port'],
        ['标准串行端口',       'Standard Serial Port'],
        ['蓝牙链路上的标准串行', 'Bluetooth Serial'],
        ['USB串行设备',       'USB Serial Device'],
        ['USB 串行设备',      'USB Serial Device'],
    ];
    for (const [zh, en] of map) {
        if (mfr.includes(zh)) return en;
    }
    return mfr;
}

// ========== IPC Handlers ==========
ipcMain.handle('get-ports', async () => {
    try {
        const ports = await SerialPort.list();
        return ports.map(port => ({
            path: port.path,
            manufacturer: sanitizeManufacturer(port.manufacturer),
            productId: port.productId || 'Unknown'
        }));
    } catch (error) {
        console.error('Failed to list ports:', error);
        return [];
    }
});

ipcMain.handle('open-port', async (event, portPath, baudRate) => {
    return await SerialHandler.openPort(portPath, baudRate);
});

ipcMain.handle('close-port', async () => {
    return await SerialHandler.closePort();
});

ipcMain.handle('calibration', async () => {
    return await SerialHandler.calibration();
});

ipcMain.handle('is-port-open', () => {
    return SerialHandler.isPortOpen();
});

// ========== Modbus RTU IPC ==========
ipcMain.handle('modbus-open', async (event, portPath, baudRate, leftId, rightId) => {
    return await ModbusHandler.openPort(portPath, baudRate, leftId, rightId);
});

ipcMain.handle('modbus-close', async () => {
    return await ModbusHandler.closePort();
});

ipcMain.handle('modbus-is-open', () => {
    return ModbusHandler.isPortOpen();
});

ipcMain.handle('modbus-calibrate', async () => {
    return await ModbusHandler.zeroCal();
});
