"""Qt dialog for generating an H3 consistency evaluation workbook."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PyQt5 import QtCore, QtWidgets

from consistency_report import generate_consistency_report


class ReportWorker(QtCore.QThread):
    progress = QtCore.pyqtSignal(int, str)
    succeeded = QtCore.pyqtSignal(dict)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, input_dir: str, output_path: str, parent=None, mode="calibration"):
        super().__init__(parent)
        self.input_dir = input_dir
        self.output_path = output_path
        self.mode = mode

    def run(self):
        try:
            result = generate_consistency_report(
                self.input_dir,
                self.output_path,
                lambda percent, message: self.progress.emit(percent, message),
                mode=self.mode,
            )
        except Exception as exc:  # GUI boundary: present actionable message.
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(result)


class ConsistencyReportDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker: ReportWorker | None = None
        self.last_output = ""
        self.setWindowTitle("H3 一致性评价报告")
        self.setMinimumWidth(720)
        self.setModal(False)
        self.setStyleSheet("""
            QDialog { background:#071116; color:#e7f1f4; }
            QWidget { font-family:'Segoe UI','Microsoft YaHei'; font-size:10pt; color:#e7f1f4; }
            QLabel { background:transparent; }
            QLabel#dialogTitle { font-size:15pt; font-weight:700; color:#e7f1f4; }
            QLabel#dialogDescription, QLabel#statusText { color:#a8c4cb; }
            QLabel#dialogNotice {
                background:#182d35; border:1px solid #31505d; border-radius:8px;
                padding:10px 12px; color:#d5e6ea;
            }
            QFrame#reportSurface {
                background:#10232c; border:1px solid #24404d; border-radius:10px;
            }
            QLineEdit, QComboBox {
                min-height:30px; background:#0a181f; border:1px solid #31505d;
                border-radius:7px; padding:3px 8px; color:#e7f1f4;
                selection-background-color:#246b70;
            }
            QLineEdit:focus { border-color:#45c9d1; }
            QLineEdit:disabled { background:#0d1b23; border-color:#24404d; color:#8aa2ac; }
            QPushButton {
                min-height:30px; background:#18313b; border:1px solid #34535f;
                border-radius:7px; padding:3px 12px; color:#e7f1f4;
            }
            QPushButton:hover { background:#213f49; border-color:#4a727e; }
            QPushButton:pressed { background:#11252d; }
            QPushButton:focus { border-color:#45c9d1; }
            QPushButton#primary { background:#176f76; border-color:#329ca4; color:#f2ffff; font-weight:600; }
            QPushButton#primary:hover { background:#21838a; border-color:#45c9d1; }
            QPushButton:disabled, QPushButton#primary:disabled {
                background:#0d1b23; border-color:#24404d; color:#8aa2ac;
            }
            QProgressBar {
                background:#0a181f; border:1px solid #31505d; border-radius:6px;
                min-height:23px; text-align:center; color:#e7f1f4;
            }
            QProgressBar::chunk { background:#45c9d1; border-radius:5px; }
        """)
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(13)

        title = QtWidgets.QLabel("每个文件夹，生成一张结果表")
        title.setObjectName("dialogTitle")
        description = QtWidgets.QLabel(
            "普通记录 CSV 可直接生成前／后 3、15、25 N 六工况报告，无需 summary。"
            "每个 CSV 作为一次采集，检查每组是否有 10 次，并统计重复性。"
        )
        description.setWordWrap(True)
        description.setObjectName("dialogDescription")
        layout.addWidget(title)
        layout.addWidget(description)

        form_panel = QtWidgets.QFrame()
        form_panel.setObjectName("reportSurface")
        form = QtWidgets.QFormLayout(form_panel)
        form.setContentsMargins(16, 16, 16, 16)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.mode_box = QtWidgets.QComboBox()
        self.mode_box.addItem("区域均值：每文件夹 10 个 CSV → Markdown", "folders_md")
        self.mode_box.addItem("标定 summary：完整标定评价", "calibration")
        self.mode_box.addItem("区域均值：每文件夹 10 个 CSV → Excel", "folders")
        form.addRow("数据类型", self.mode_box)
        self.input_edit = QtWidgets.QLineEdit()
        self.input_edit.setPlaceholderText("选择 CSV 目录，或粘贴一个 CSV 的完整路径")
        input_button = QtWidgets.QPushButton("选择目录")
        input_button.clicked.connect(self._choose_input)
        file_button = QtWidgets.QPushButton("选择 CSV")
        file_button.clicked.connect(self._choose_file)
        input_row = QtWidgets.QHBoxLayout()
        input_row.addWidget(self.input_edit, 1)
        input_row.addWidget(input_button)
        input_row.addWidget(file_button)
        form.addRow("数据路径", input_row)

        self.output_edit = QtWidgets.QLineEdit()
        self.output_edit.setPlaceholderText("保存结果表的目录")
        self.output_button = QtWidgets.QPushButton("选择输出目录")
        self.output_button.clicked.connect(self._choose_output)
        output_row = QtWidgets.QHBoxLayout()
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(self.output_button)
        self.output_label = QtWidgets.QLabel("输出目录")
        form.addRow(self.output_label, output_row)
        layout.addWidget(form_panel)

        note = QtWidgets.QLabel()
        note.setWordWrap(True)
        note.setObjectName("dialogNotice")
        layout.addWidget(note)
        def update_description():
            recording = self.mode_box.currentData() in ("folders", "folders_md")
            markdown = self.mode_box.currentData() == "folders_md"
            description.setText(
                ("选择一个文件夹或总目录。每个文件夹生成一份MD报告，包含10轮区域均值、FS%MAX、FS%mean、公式与源文件核对。" if markdown else
                 "选择一个文件夹或总目录。每个文件夹独立输出 Excel，10 个 CSV 分别填入 Sheet1 的 10 行；每列是该区域在单个文件中的算术平均值。")
                if recording else
                "读取标定 summary.csv 或 *_summary.csv；frames.csv 可选。生成精度、重复性、迟滞及卸载残余等指标。缺少必要数据时不判合格。"
            )
            note.setText(
                ("FS固定30 N；各轮等权，使用全部有效采样。直接读取CSV已保存区域值，不重新套用当前校正。区域为估计分配；不足10轮会明确提示。" if markdown else
                 "沿用 NO7 的均值与偏差公式，FS=30 N。H3 保留 S1–S8 八区，NO7 保留 P00–P11 十二区。Sheet1 均值公式直接关联“逐帧区域值”，可按 CSV 行号抽查。每区查看第42行最大偏差、第46行平均偏差。区域值为算法分配估计，质量提示照实保留。")
                if recording else
                "每台设备使用唯一 device_sn。只有一台设备时不能评价设备间一致性；只有表头的 CSV 会列为无采样并跳过。"
            )
            self.output_label.setText("输出目录" if recording else "输出文件")
            self.output_button.setText("选择输出目录" if recording else "另存为")
            self.output_edit.setPlaceholderText("保存各文件夹结果表的目录" if recording else "生成的 .xlsx 文件")
            if self.output_edit.text():
                self.output_edit.setText(self._default_output(self.input_edit.text()))
            if hasattr(self, 'generate_button'):
                self.generate_button.setText('生成 Markdown 报告' if markdown else '生成一致性评价 Excel')
        self.mode_box.currentIndexChanged.connect(update_description)
        update_description()

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("等待选择数据目录")
        layout.addWidget(self.progress_bar)

        self.status_label = QtWidgets.QLabel("原始 CSV 只读，不会被修改。")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("statusText")
        layout.addWidget(self.status_label)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(9)
        self.open_button = QtWidgets.QPushButton("打开输出目录")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open_output_dir)
        buttons.addWidget(self.open_button)
        buttons.addStretch()
        close_button = QtWidgets.QPushButton("关闭")
        close_button.clicked.connect(self.close)
        self.generate_button = QtWidgets.QPushButton("生成 Markdown 报告")
        self.generate_button.setObjectName("primary")
        self.generate_button.clicked.connect(self._generate)
        buttons.addWidget(close_button)
        buttons.addWidget(self.generate_button)
        layout.addLayout(buttons)

    def _default_output(self, directory: str) -> str:
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"区域均值结果_{stamp}" if self.mode_box.currentData() in ("folders", "folders_md") else f"H3设备一致性评价_{stamp}.xlsx"
        root = Path(directory.strip().strip('"'))
        return str((root.parent if root.is_file() else root) / filename)

    def _choose_input(self):
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "选择本批采集 CSV 目录", self.input_edit.text())
        if not directory:
            return
        self.input_edit.setText(directory)
        if not self.output_edit.text():
            self.output_edit.setText(self._default_output(directory))
        self.progress_bar.setFormat("已选择数据目录，等待生成")

    def _choose_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择一个采集 CSV", self.input_edit.text(), "CSV 文件 (*.csv)")
        if path:
            self.input_edit.setText(path)
            if not self.output_edit.text():
                self.output_edit.setText(self._default_output(path))

    def _choose_output(self):
        suggested = self.output_edit.text() or self._default_output(self.input_edit.text() or str(Path.home()))
        if self.mode_box.currentData() in ("folders", "folders_md"):
            path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择结果表保存目录", suggested)
            if path:
                self.output_edit.setText(path)
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "保存一致性评价 Excel", suggested, "Excel 工作簿 (*.xlsx)")
        if path:
            self.output_edit.setText(path if path.lower().endswith(".xlsx") else path + ".xlsx")

    def _generate(self):
        input_dir = self.input_edit.text().strip().strip('"')
        output_path = self.output_edit.text().strip()
        if not input_dir or not Path(input_dir).exists():
            QtWidgets.QMessageBox.warning(self, "数据路径无效", "请选择有效的 CSV 文件或目录。")
            return
        if not output_path:
            output_path = self._default_output(input_dir)
            self.output_edit.setText(output_path)
        if self.mode_box.currentData() not in ("folders", "folders_md") and not output_path.lower().endswith(".xlsx"):
            output_path += ".xlsx"
            self.output_edit.setText(output_path)
        self.generate_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.progress_bar.setValue(1)
        self.progress_bar.setFormat("开始扫描 CSV…")
        self.status_label.setText("正在后台生成，串口监视和采集界面不会被阻塞。")
        self.worker = ReportWorker(input_dir, output_path, self, mode=self.mode_box.currentData())
        self.worker.progress.connect(self._on_progress)
        self.worker.succeeded.connect(self._on_success)
        self.worker.failed.connect(self._on_failure)
        self.worker.finished.connect(lambda: self.generate_button.setEnabled(True))
        self.worker.start()

    def _on_progress(self, percent: int, message: str):
        self.progress_bar.setValue(percent)
        self.progress_bar.setFormat(f"{percent}% · {message}")

    def _on_success(self, result: dict):
        self.last_output = result["path"]
        self.open_button.setEnabled(True)
        self.progress_bar.setValue(100)
        self.progress_bar.setFormat("100% · 报告生成完成")
        if result.get("report_mode") == "folders":
            self.status_label.setText(
                f"已生成 {result['report_count']} 张结果表，填入 {result['file_count']} 个 CSV 的区域均值。"
                f"未导入 {result['skipped_files']} 个文件，未生成 {result['failed_folders']} 个文件夹。"
                + ("MD包含各轮区域均值、FS指标、公式和原文件核对。" if result.get('output_format') == 'md' else
                 "Sheet1 为计算结果；输入明细列出源文件与列名；逐帧区域值可按 CSV 行号抽查。")
                + ("\n" + "\n".join(result.get("errors", [])) if result.get("errors") else "")
            )
        elif result.get("report_mode") == "recordings":
            self.status_label.setText(
                f"已生成：{Path(self.last_output).name}；导入 {result['file_count']} 个 CSV，"
                f"数量齐全 {result['complete_groups']}/6 组，未导入 {result['skipped_files']} 个文件。"
                "未导入原因见 Excel 的“导入说明”。"
            )
        else:
            self.status_label.setText(
                f"已生成：{Path(self.last_output).name}；设备 {result['device_count']} 台，"
                f"有效会话 {result['session_count']} 个，跳过空文件 {result['skipped_empty']} 个，"
                f"修复旧表头 {result['recovered_headers']} 个。"
            )
        QtWidgets.QMessageBox.information(self, "生成完成", self.status_label.text())

    def _on_failure(self, message: str):
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("生成失败")
        self.status_label.setText(f"生成失败：{message}")
        QtWidgets.QMessageBox.critical(
            self,
            "无法生成一致性评价",
            message,
        )

    def _open_output_dir(self):
        if not self.last_output:
            return
        path = Path(self.last_output)
        directory = str(path if path.is_dir() else path.parent)
        os.startfile(directory)

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            QtWidgets.QMessageBox.information(self, "报告正在生成", "请等待当前报告生成完成后再关闭窗口。")
            event.ignore()
            return
        event.accept()

