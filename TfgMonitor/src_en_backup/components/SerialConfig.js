import React, { useState, useEffect } from 'react';

const SerialConfig = ({ onConnectionChange }) => {
    const [ports, setPorts] = useState([]);
    const [selectedPort, setSelectedPort] = useState('');
    const [baudRate, setBaudRate] = useState('115200');
    const [isConnected, setIsConnected] = useState(false);
    const [isLoading, setIsLoading] = useState(false);

    const baudRates = ['115200','921600','2000000'];

    useEffect(() => {
        loadPorts();
    }, []);

    const loadPorts = async () => {
        if (!window.electron || !window.electron.ipcRenderer) {
            console.error('Electron context not available');
            return;
        }

        try {
            const portList = await window.electron.ipcRenderer.invoke('get-ports');
            setPorts(portList);
            if (portList.length > 0 && !selectedPort) {
                setSelectedPort(portList[0].path);
            }
        } catch (error) {
            console.error('Failed to load ports:', error);
        }
    };

    const handleConnect = async () => {
        if (!selectedPort) return;

        setIsLoading(true);
        try {
            console.log(baudRate);
            await window.electron.ipcRenderer.invoke('open-port', selectedPort, parseInt(baudRate, 10));
            setIsConnected(true);
            onConnectionChange(true);
        } catch (error) {
            console.error('Failed to connect:', error);
            alert('连接失败: ' + error.message || error);
        } finally {
            setIsLoading(false);
        }
    };

    const handleDisconnect = async () => {
        setIsLoading(true);
        try {
            await window.electron.ipcRenderer.invoke('close-port');
            setIsConnected(false);
            onConnectionChange(false);
        } catch (error) {
            console.error('Failed to disconnect:', error);
        } finally {
            setIsLoading(false);
        }
    };

    // 添加标定按钮处理函数
    const handleCalibration = async () => {
        if (!isConnected) return;

        try {
            // 向主进程发送calib命令
            await window.electron.ipcRenderer.invoke('calibration');
            console.log('标定命令已发送');
        } catch (error) {
            console.error('发送标定命令失败:', error);
            alert('发送标定命令失败: ' + error.message || error);
        }
    };

    return (
        <div className="serial-config">
            <select
                value={selectedPort}
                onChange={(e) => setSelectedPort(e.target.value)}
                disabled={isConnected || isLoading}
            >
                <option value="">选择串口</option>
                {ports.map((port) => (
                    <option key={port.path} value={port.path}>
                        {port.path} - {port.manufacturer || '未知设备'}
                    </option>
                ))}
            </select>

            <select
                value={baudRate}
                onChange={(e) => setBaudRate(e.target.value)}
                disabled={isConnected || isLoading}
            >
                {baudRates.map((rate) => (
                    <option key={rate} value={rate}>
                        {rate} baud
                    </option>
                ))}
            </select>

            <button
                onClick={loadPorts}
                disabled={isLoading}
                className="refresh-btn"
            >
                {isLoading ? '刷新中...' : '刷新'}
            </button>

            {/* 标定按钮始终显示，但根据连接状态启用/禁用 */}
            <button
                onClick={handleCalibration}
                disabled={!isConnected || isLoading}
                className="calibrate-btn"
            >
                标定
            </button>

            {isConnected ? (
                <button
                    onClick={handleDisconnect}
                    disabled={isLoading}
                    className="disconnect-btn"
                >
                    {isLoading ? '断开中...' : '断开连接'}
                </button>
            ) : (
                <button
                    onClick={handleConnect}
                    disabled={!selectedPort || isLoading}
                >
                    {isLoading ? '连接中...' : '连接'}
                </button>
            )}
        </div>
    );
};

export default SerialConfig;