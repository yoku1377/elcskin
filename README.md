# elcskin

电子皮肤项目，包含力/触觉传感器数据的监测与可视化工具。

## 目录

- `TfgMonitor/`：基于 Electron、React 的监控应用。
- `TacAI-H2.1IPC/`：基于 PyQt5 的触觉传感器三维力可视化程序。
- `resources/`：项目资源文件。
- [TacAI-H3.0-v1/](TacAI-H3.0-v1/)：H3 上位机、固件、标定采集与 ZP-500N 自动参考。

## H3 Windows 下载

[下载 H3 v5.3.28 EXE 与校验文件](https://github.com/yoku1377/elcskin/releases/tag/h3-v5.3.28-20261021)

## 开发

### TfgMonitor

```bash
cd TfgMonitor
npm install
npm start
```

### TacAI-H2.1IPC

安装 Python 依赖（包括 `PyQt5`）后运行：

```bash
cd TacAI-H2.1IPC
python main.py
```
