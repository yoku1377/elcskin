"""生成约 19 万中文字符的测试 Excel，用于验证知识库分批 embedding 配置"""
import openpyxl
import random

# 模拟知识库文章段落内容的中文模板
TEMPLATES = [
    "触觉传感器是一种能够感知压力、温度、振动等物理量的传感器，广泛应用于机器人、医疗设备和可穿戴设备领域。",
    "柔性电子皮肤由多层导电材料和弹性基底组成，能够模拟人类皮肤对外界刺激的感知能力，具有良好的延展性和灵敏度。",
    "信号采集模块通过模数转换器将传感器输出的模拟信号转换为数字信号，采样率通常在一百赫兹到一万赫兹之间。",
    "数据处理算法包括滤波、特征提取和分类识别等步骤，常用的方法有卡尔曼滤波、小波变换和支持向量机等。",
    "无线通信模块采用蓝牙低功耗协议进行数据传输，传输距离可达十米，功耗极低，适合长时间佩戴的可穿戴设备。",
    "压阻式传感器利用材料在受力时电阻发生变化的原理进行测量，具有结构简单、成本低廉的优点。",
    "电容式触觉传感器通过检测电极间电容变化来感知压力分布，具有高灵敏度和快速响应的特点。",
    "人工智能算法在传感器数据分析中发挥重要作用，深度学习模型能够从复杂的传感数据中提取有效特征。",
    "嵌入式系统设计需要综合考虑处理器性能、内存容量、功耗和实时性等多方面因素。",
    "工业检测领域对传感器的精度和可靠性要求极高，通常需要进行严格的标定和温度补偿处理。",
    "医疗级别的触觉传感系统需要符合相关安全标准，确保在人体接触时不会造成任何伤害。",
    "机器学习模型的训练需要大量标注数据，数据增强技术可以有效扩充训练集规模。",
    "传感器阵列由多个感知单元组成，能够同时采集空间分布的压力信息，实现二维甚至三维的感知。",
    "实时数据可视化系统能够将传感器采集的数据以图表形式直观展示，帮助用户快速了解系统状态。",
    "电源管理模块负责为系统各部分提供稳定的工作电压，包括线性稳压器和开关电源等不同方案。",
]

wb = openpyxl.Workbook()
ws = wb.active
ws.title = "测试数据"
ws.append(["序号", "标题", "内容", "类别", "备注"])

TARGET_CHARS = 190_000
total_chars = 0
row_id = 1

categories = ["传感器技术", "信号处理", "嵌入式系统", "人工智能", "医疗应用"]

while total_chars < TARGET_CHARS:
    # 每行内容：随机拼接 3~6 个模板段落，模拟真实文章段落
    num_para = random.randint(3, 6)
    content = "".join(random.choices(TEMPLATES, k=num_para))
    title = f"技术文档第{row_id:04d}篇：" + random.choice(TEMPLATES)[:15] + "..."
    note = f"测试数据行{row_id}，用于验证知识库embedding分批处理功能。"
    category = random.choice(categories)

    ws.append([row_id, title, content, category, note])
    total_chars += len(content) + len(title) + len(note)
    row_id += 1

output_path = r"d:\202605\5.电子皮肤\TfgMonitor\test_190k_tokens.xlsx"
wb.save(output_path)
print(f"生成完成：{row_id - 1} 行，总字符数约 {total_chars:,}，文件：{output_path}")
