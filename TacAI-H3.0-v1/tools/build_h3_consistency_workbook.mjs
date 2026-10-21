import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [jsonPath, outputPath] = process.argv.slice(2);
if (!jsonPath || !outputPath) throw new Error("Usage: node build_h3_consistency_workbook.mjs input.json output.xlsx");
const data = JSON.parse(await fs.readFile(jsonPath, "utf8"));

const wb = Workbook.create();
const dashboard = wb.worksheets.add("综合评价");
const settings = wb.worksheets.add("判定参数");
const devices = wb.worksheets.add("设备指标");
const points = wb.worksheets.add("位置与力点");
const consistency = wb.worksheets.add("设备间一致性");
const raw = wb.worksheets.add("原始步汇总");
const sources = wb.worksheets.add("来源说明");

const font = "Arial";
const navy = "#17365D";
const blue = "#1F4E78";
const lightBlue = "#D9EAF7";
const amber = "#FFF2CC";
const green = "#E2F0D9";
const red = "#FCE4D6";
const gray = "#F2F2F2";
const border = "#D9E2F3";

function title(sheet, text, lastCol = "H") {
  sheet.showGridLines = false;
  sheet.getRange(`A1:${lastCol}1`).format.rowHeight = 8;
  sheet.getRange("A2").values = [[text]];
  sheet.getRange("A2").format.font = { name: font, size: 16, bold: true, color: navy };
  sheet.getRange(`A3:${lastCol}3`).format.borders = { bottom: { style: "thin", color: navy } };
  sheet.getRange("A4").format.rowHeight = 8;
}

function header(range) {
  range.format = {
    fill: navy,
    font: { name: font, size: 10, bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "inside", style: "thin", color: "#FFFFFF" },
  };
  range.format.rowHeight = 30;
}

function body(range) {
  range.format.font = { name: font, size: 10, color: "#222222" };
  range.format.verticalAlignment = "center";
  range.format.borders = { insideHorizontal: { style: "thin", color: border } };
}

function section(sheet, row, text, lastCol) {
  const range = sheet.getRange(`A${row}:${lastCol}${row}`);
  sheet.getRange(`A${row}`).values = [[text]];
  range.format = { fill: lightBlue, font: { name: font, size: 11, bold: true, color: navy } };
  range.format.borders = { preset: "outside", style: "thin", color: blue };
  range.format.rowHeight = 24;
}

// Editable thresholds and ranking weights.
title(settings, "H3一致性评价参数", "D");
settings.getRange("A5:D5").values = [["指标", "试行阈值", "排名权重", "判定方向"]];
header(settings.getRange("A5:D5"));
const rules = [
  ["整体MAE（N）", 1.0, 20, "不大于"],
  ["2N及以下MAE（N）", 0.5, 20, "不大于"],
  ["误差≤1N比例", 0.9, 10, "不小于"],
  ["重复性P95（N）", 0.5, 15, "不大于"],
  ["迟滞P95（N）", 1.0, 10, "不大于"],
  ["卸载残余最大值（N）", 0.01, 15, "不大于"],
  ["前中后差异P95（N）", 1.0, 10, "不大于"],
];
settings.getRange("A6:D12").values = rules;
body(settings.getRange("A6:D12"));
settings.getRange("B6:C12").format.fill = amber;
settings.getRange("B6:B12").format.numberFormat = "0.000";
settings.getRange("C6:C12").format.numberFormat = "0";
settings.getRange("A14:D16").values = [
  ["说明", null, null, null],
  ["黄色单元格可修改；全部指标满足才判为合格。", null, null, null],
  ["0.01 N为回零目标，其余为试行阈值，供样前再确认。", null, null, null],
];
settings.getRange("A14:D14").format = { fill: gray, font: { name: font, bold: true, color: navy } };
settings.getRange("A15:D16").format.font = { name: font, size: 10, italic: true, color: "#555555" };
settings.getRange("A15:D16").format.wrapText = false;
settings.getRange("A15:D16").format.rowHeight = 22;
settings.getRange("A:D").format.columnWidth = 22;
settings.getRange("A:A").format.columnWidth = 30;
settings.getRange("D:D").format.columnWidth = 16;

// Device metrics and formula-driven pass/rank.
title(devices, "设备指标与选优排名", "U");
const deviceHeaders = ["设备", "硅胶批次", "磁铁组件", "测试人", "会话数", "轮数", "位置数", "受力步骤", "整体MAE(N)", "≤2N MAE(N)", "误差≤1N比例", "误差P95(N)", "最大误差(N)", "重复性P95(N)", "迟滞P95(N)", "卸载残余中位(N)", "卸载残余P95(N)", "卸载残余最大(N)", "前中后差异P95(N)", "异常步骤", "综合分", "排名", "判定"];
devices.getRangeByIndexes(4, 0, 1, deviceHeaders.length).values = [deviceHeaders];
header(devices.getRangeByIndexes(4, 0, 1, deviceHeaders.length));
const metricRows = data.device_metrics.map(d => [d.device_id, d.silicone_batch, d.magnet_assembly, d.operator, d.session_count, d.repeat_count, d.position_count, d.loaded_steps, d.overall_mae_n, d.low_force_mae_n, d.within_1n_rate, d.p95_abs_error_n, d.max_abs_error_n, d.repeatability_p95_n, d.hysteresis_p95_n, d.residual_median_n, d.residual_p95_n, d.residual_max_n, d.position_spread_p95_n, d.bad_quality_steps, null, null, null]);
if (metricRows.length) {
  devices.getRangeByIndexes(5, 0, metricRows.length, deviceHeaders.length).values = metricRows;
  const first = 6;
  const last = first + metricRows.length - 1;
  for (let row = first; row <= last; row++) {
    devices.getRange(`U${row}`).formulas = [[`=IF(COUNT(I${row},J${row},K${row},N${row},O${row},R${row},S${row})=7,ROUND(100*(MIN(1,'判定参数'!$B$6/MAX(I${row},0.000000001))*'判定参数'!$C$6+MIN(1,'判定参数'!$B$7/MAX(J${row},0.000000001))*'判定参数'!$C$7+IF('判定参数'!$B$8<=0,1,MIN(1,K${row}/'判定参数'!$B$8))*'判定参数'!$C$8+MIN(1,'判定参数'!$B$9/MAX(N${row},0.000000001))*'判定参数'!$C$9+MIN(1,'判定参数'!$B$10/MAX(O${row},0.000000001))*'判定参数'!$C$10+MIN(1,'判定参数'!$B$11/MAX(R${row},0.000000001))*'判定参数'!$C$11+MIN(1,'判定参数'!$B$12/MAX(S${row},0.000000001))*'判定参数'!$C$12)/MAX(SUM('判定参数'!$C$6:$C$12),0.000000001),1),"")`]];
    devices.getRange(`V${row}`).formulas = [[`=IF(U${row}="","",1+COUNTIF($U$${first}:$U$${last},">"&U${row}))`]];
    devices.getRange(`W${row}`).formulas = [[`=IF(COUNT(I${row},J${row},K${row},N${row},O${row},R${row},S${row})<7,"数据不足",IF(AND(I${row}<='判定参数'!$B$6,J${row}<='判定参数'!$B$7,K${row}>='判定参数'!$B$8,N${row}<='判定参数'!$B$9,O${row}<='判定参数'!$B$10,R${row}<='判定参数'!$B$11,S${row}<='判定参数'!$B$12,T${row}=0),"合格","不合格"))`]];
  }
  body(devices.getRangeByIndexes(5, 0, metricRows.length, deviceHeaders.length));
  devices.getRange(`I6:T${last}`).format.numberFormat = "0.000";
  devices.getRange(`K6:K${last}`).format.numberFormat = "0.0%";
  devices.getRange(`U6:U${last}`).format.numberFormat = "0.0";
  devices.getRange(`W6:W${last}`).conditionalFormats.add("containsText", { text: "不合格", format: { fill: red, font: { bold: true, color: "#9C0006" } } });
  devices.getRange(`W6:W${last}`).conditionalFormats.add("containsText", { text: "合格", format: { fill: green, font: { bold: true, color: "#006100" } } });
}
devices.freezePanes.freezeRows(5);
devices.freezePanes.freezeColumns(1);
devices.getRange("A:W").format.columnWidth = 14;
devices.getRange("A:D").format.columnWidth = 16;
devices.getRange("A:A").format.columnWidth = 20;

// Dashboard.
title(dashboard, "H3设备一致性评价", "N");
dashboard.getRange("A5:B10").values = [
  ["设备数量", null], ["合格数量", null], ["最佳设备", null], ["最大低力MAE(N)", null], ["最大卸载残余(N)", null], ["数据状态", null],
];
dashboard.getRange("A5:A10").format = { fill: lightBlue, font: { name: font, bold: true, color: navy } };
dashboard.getRange("B5:B10").format = { font: { name: font, size: 12, bold: true, color: "#222222" }, fill: "#FFFFFF" };
const deviceLast = 5 + Math.max(1, metricRows.length);
dashboard.getRange("B5").formulas = [[`=COUNTA('设备指标'!A6:A${deviceLast})`]];
dashboard.getRange("B6").formulas = [[`=COUNTIF('设备指标'!W6:W${deviceLast},"合格")`]];
const defaultThresholds = [1.0, 0.5, 0.9, 0.5, 1.0, 0.01, 1.0];
const defaultWeights = [20, 20, 10, 15, 10, 15, 10];
function defaultScore(d) {
  const values = [d.overall_mae_n, d.low_force_mae_n, d.within_1n_rate, d.repeatability_p95_n, d.hysteresis_p95_n, d.residual_max_n, d.position_spread_p95_n];
  if (values.some(value => value === null || !Number.isFinite(Number(value)))) return -1;
  const completion = values.map((value, index) => index === 2 ? Math.min(1, value / defaultThresholds[index]) : Math.min(1, defaultThresholds[index] / Math.max(value, 1e-12)));
  return completion.reduce((sum, value, index) => sum + value * defaultWeights[index], 0) / defaultWeights.reduce((a, b) => a + b, 0);
}
const defaultBest = [...data.device_metrics].sort((a, b) => defaultScore(b) - defaultScore(a))[0];
dashboard.getRange("B7").values = [[defaultBest ? defaultBest.device_id : "无数据"]];
dashboard.getRange("B8").formulas = [[`=IF(B5=0,"n.a.",MAX('设备指标'!J6:J${deviceLast}))`]];
dashboard.getRange("B9").formulas = [[`=IF(B5=0,"n.a.",MAX('设备指标'!R6:R${deviceLast}))`]];
dashboard.getRange("B10").formulas = [[data.device_consistency.length ? `=IF(B5>=2,"已生成设备间一致性","设备不足2台")` : `="设备不足2台，暂不评价设备间一致性"`]];
dashboard.getRange("B8:B9").format.numberFormat = "0.000";
section(dashboard, 12, "设备排名", "H");
dashboard.getRange("A13:H13").values = [["排名", "设备", "综合分", "判定", "整体MAE(N)", "≤2N MAE(N)", "误差≤1N比例", "卸载残余最大(N)"]];
header(dashboard.getRange("A13:H13"));
for (let i = 0; i < metricRows.length; i++) {
  const row = 14 + i;
  const sourceRow = 6 + i;
  dashboard.getRange(`A${row}:H${row}`).formulas = [[`='设备指标'!V${sourceRow}`, `='设备指标'!A${sourceRow}`, `='设备指标'!U${sourceRow}`, `='设备指标'!W${sourceRow}`, `='设备指标'!I${sourceRow}`, `='设备指标'!J${sourceRow}`, `='设备指标'!K${sourceRow}`, `='设备指标'!R${sourceRow}`]];
}
if (metricRows.length) {
  body(dashboard.getRange(`A14:H${13 + metricRows.length}`));
  dashboard.getRange(`C14:C${13 + metricRows.length}`).format.numberFormat = "0.0";
  dashboard.getRange(`E14:F${13 + metricRows.length}`).format.numberFormat = "0.000";
  dashboard.getRange(`G14:G${13 + metricRows.length}`).format.numberFormat = "0.0%";
  dashboard.getRange(`H14:H${13 + metricRows.length}`).format.numberFormat = "0.000";
  dashboard.getRange(`D14:D${13 + metricRows.length}`).conditionalFormats.add("containsText", { text: "不合格", format: { fill: red, font: { bold: true, color: "#9C0006" } } });
}
dashboard.getRange("A:H").format.columnWidth = 17;
dashboard.getRange("A:A").format.columnWidth = 23;
dashboard.getRange("B:B").format.columnWidth = 23;
if (metricRows.length) {
  const scoreChart = dashboard.charts.add("bar", [
    dashboard.getRange(`B13:B${13 + metricRows.length}`),
    dashboard.getRange(`C13:C${13 + metricRows.length}`),
  ]);
  scoreChart.title = "设备综合分";
  scoreChart.titleTextStyle.fontSize = 12;
  scoreChart.titleTextStyle.typeface = font;
  scoreChart.hasLegend = false;
  scoreChart.xAxis = { axisType: "textAxis", textStyle: { typeface: font, fontSize: 10 } };
  scoreChart.yAxis = { numberFormatCode: "0.0", numberFormatSourceLinked: false, textStyle: { typeface: font, fontSize: 10 } };
  scoreChart.setPosition("J5", "N16");
}

// Position and force-point detail.
title(points, "位置与力点统计", "K");
section(points, 5, "前中后位置指标", "G");
points.getRange("A6:G6").values = [["设备", "位置", "步骤数", "MAE(N)", "≤2N MAE(N)", "偏差(N)", "误差≤1N比例"]];
header(points.getRange("A6:G6"));
const posRows = data.position_metrics.map(r => [r.device_id, r.position, r.steps, r.mae_n, r.low_force_mae_n, r.bias_n, r.within_1n_rate]);
if (posRows.length) {
  points.getRangeByIndexes(6, 0, posRows.length, 7).values = posRows;
  body(points.getRangeByIndexes(6, 0, posRows.length, 7));
  points.getRangeByIndexes(6, 3, posRows.length, 3).format.numberFormat = "0.000";
  points.getRangeByIndexes(6, 6, posRows.length, 1).format.numberFormat = "0.0%";
}
const pointStart = 9 + posRows.length;
section(points, pointStart, "各力点统计", "J");
points.getRange(`A${pointStart + 1}:J${pointStart + 1}`).values = [["设备", "位置", "方向", "真实力(N)", "样本数", "输出均值(N)", "输出标准差(N)", "偏差(N)", "MAE(N)", "低力标记"]];
header(points.getRange(`A${pointStart + 1}:J${pointStart + 1}`));
const fpRows = data.force_points.map(r => [r.device_id, r.position, r.direction, r.reference_n, r.samples, r.output_mean_n, r.output_std_n, r.bias_n, r.mae_n, r.reference_n <= 2 ? "≤2N" : ""]);
if (fpRows.length) {
  points.getRangeByIndexes(pointStart + 1, 0, fpRows.length, 10).values = fpRows;
  body(points.getRangeByIndexes(pointStart + 1, 0, fpRows.length, 10));
  points.getRangeByIndexes(pointStart + 1, 3, fpRows.length, 6).format.numberFormat = "0.000";
}
points.freezePanes.freezeRows(6);
points.getRange("A:J").format.columnWidth = 16;

// Device consistency detail.
title(consistency, "设备间一致性", "H");
consistency.getRange("A5:H5").values = [["位置", "方向", "真实力(N)", "设备数", "设备均值(N)", "设备标准差(N)", "相对真实力CV", "设备极差(N)"]];
header(consistency.getRange("A5:H5"));
if (data.device_consistency.length) {
  const rows = data.device_consistency.map(r => [r.position, r.direction, r.reference_n, r.device_count, r.device_mean_n, r.device_std_n, r.cv_percent_of_reference / 100, r.device_spread_n]);
  consistency.getRangeByIndexes(5, 0, rows.length, 8).values = rows;
  body(consistency.getRangeByIndexes(5, 0, rows.length, 8));
  consistency.getRangeByIndexes(5, 2, rows.length, 4).format.numberFormat = "0.000";
  consistency.getRangeByIndexes(5, 6, rows.length, 1).format.numberFormat = "0.0%";
  consistency.getRangeByIndexes(5, 7, rows.length, 1).format.numberFormat = "0.000";
} else {
  consistency.mergeCells("A6:H6");
  consistency.mergeCells("A7:H7");
  consistency.getRange("A6").values = [["至少导入2台不同设备的数据后，本表自动生成设备间标准差、CV和极差。"]];
  consistency.getRange("A7").values = [["设备编号取CSV中的device_sn；多台设备请填写不同编号。"]];
  consistency.getRange("A6:H7").format = { fill: amber, font: { name: font, color: "#7F6000" }, wrapText: true };
  consistency.getRange("A6:H7").format.rowHeight = 28;
}
consistency.freezePanes.freezeRows(5);
consistency.getRange("A:H").format.columnWidth = 18;

// Normalized source rows.
title(raw, "原始步汇总（标准化）", "O");
const rawHeaders = ["设备", "会话", "完成时间", "轮次", "位置", "方向", "目标力(N)", "真实力(N)", "模型输出(N)", "误差(N)", "绝对误差(N)", "温度(℃)", "质量", "卸载后零点", "来源文件"];
raw.getRangeByIndexes(4, 0, 1, rawHeaders.length).values = [rawHeaders];
header(raw.getRangeByIndexes(4, 0, 1, rawHeaders.length));
const rawRows = data.raw_steps.map(r => [r.device_id, r.session_id, r.completed_time_iso, r.repeat, r.position_norm, r.direction_norm, r.target_n, r.reference_median_n, r.upper_model_median_n, r.error_n, r.abs_error_n, r.temperature_c, r.quality_norm, r.post_unload_zero ? "是" : "", r.source_file]);
if (rawRows.length) {
  raw.getRangeByIndexes(5, 0, rawRows.length, rawHeaders.length).values = rawRows;
  body(raw.getRangeByIndexes(5, 0, rawRows.length, rawHeaders.length));
  raw.getRangeByIndexes(5, 6, rawRows.length, 6).format.numberFormat = "0.000";
}
raw.freezePanes.freezeRows(5);
raw.freezePanes.freezeColumns(2);
raw.getRange("A:N").format.columnWidth = 14;
raw.getRange("B:C").format.columnWidth = 21;
raw.getRange("O:O").format.columnWidth = 52;

// Sources and method.
title(sources, "数据来源与计算口径", "H");
section(sources, 5, "导入文件", "H");
sources.getRange("A6:H6").values = [["会话", "summary文件", "frames文件", "summary行数", "frames行数", "旧表头修复", "状态", "备注"]];
header(sources.getRange("A6:H6"));
const sourceRows = data.sources.map(s => [s.session_id, s.summary_file, s.frames_file, s.summary_rows, s.frame_rows, s.header_recovered ? "是" : "否", s.status, s.summary_rows ? "已纳入计算" : "未纳入计算"]);
if (sourceRows.length) {
  sources.getRangeByIndexes(6, 0, sourceRows.length, 8).values = sourceRows;
  body(sources.getRangeByIndexes(6, 0, sourceRows.length, 8));
}
const methodStart = 9 + sourceRows.length;
section(sources, methodStart, "计算口径", "H");
const methods = [
  ["整体MAE", "所有真实力大于0的步骤，|模型输出－真实力|的平均值。"],
  ["≤2N MAE", "真实力大于0且不超过2N的步骤，绝对误差平均值。"],
  ["重复性P95", "同一设备、位置、方向、真实力在不同轮次的输出标准差，其95分位数。"],
  ["迟滞P95", "同一设备、轮次、位置和真实力下，加载与卸载输出差的绝对值，其95分位数。"],
  ["卸载残余", "同一位置和轮次出现过受力后，再次真实力为0的步骤输出绝对值。"],
  ["前中后差异P95", "同一设备、会话、轮次、方向和真实力下，三个位置输出最大值与最小值之差，其95分位数。"],
  ["设备间一致性", "同一位置、方向和真实力下，各设备输出中位数的标准差、相对真实力CV和极差。至少需要2台设备。"],
];
sources.getRangeByIndexes(methodStart, 0, methods.length, 2).values = methods;
body(sources.getRangeByIndexes(methodStart, 0, methods.length, 2));
sources.getRangeByIndexes(methodStart, 0, methods.length, 1).format.font = { name: font, bold: true, color: navy };
sources.getRange("A:H").format.columnWidth = 18;
sources.getRange("B:C").format.columnWidth = 48;
sources.getRange(`A${methodStart}:B${methodStart + methods.length - 1}`).format.wrapText = true;
sources.freezePanes.freezeRows(6);

// Workbook-wide final polish.
for (const sheet of [dashboard, settings, devices, points, consistency, raw, sources]) {
  const used = sheet.getUsedRange();
  if (used) used.format.verticalAlignment = "center";
  sheet.tabColor = sheet === dashboard ? blue : (sheet === settings ? "#FFC000" : "#5B9BD5");
}

wb.recalculate();
const errorScan = await wb.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});
console.log(errorScan.ndjson);
await fs.mkdir(new URL(".", `file:///${outputPath.replaceAll("\\", "/")}`).pathname, { recursive: true }).catch(() => {});
const output = await SpreadsheetFile.exportXlsx(wb);
await output.save(outputPath);
await fs.rm(`${outputPath}.inspect.ndjson`, { force: true });
console.log(`Saved ${outputPath}`);
