const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electron', {
    ipcRenderer: {
        // 发送同步消息（fire and forget）
        send: (channel, data) => {
            ipcRenderer.send(channel, data);
        },

        // 监听事件（如 serial-data）
        on: (channel, listener) => {
            ipcRenderer.on(channel, (event, ...args) => listener(event, ...args));
        },

        // 移除监听器
        removeAllListeners: (channel) => {
            ipcRenderer.removeAllListeners(channel);
        },

        // 支持 invoke -> handle 模式（用于异步请求+响应）
        invoke: (channel, ...args) => {
            return ipcRenderer.invoke(channel, ...args);
        }
    }
});