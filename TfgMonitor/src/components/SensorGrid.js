import React, { useMemo } from 'react';
import '../styles/SensorGrid.css';

const SensorGrid = ({ sensorData, compact, embedded }) => {
    // 提取z轴分量并插值到16×16
    const interpolatedData = useMemo(() => {
        if (!sensorData || sensorData.length !== 6) return null; // 3x2 = 6

        // 提取z值并转换为3×2矩阵（列优先：先填列，再换行）
        const zValues3x2 = [];
        for (let i = 0; i < 3; i++) {
            zValues3x2[i] = [];
            for (let j = 0; j < 2; j++) {
                const new_i = 2- i===0? 1:i===1? 0:2;
                const index = 1-j + new_i * 2; // 列主序：j 是列，i 是行
                zValues3x2[i][j] = Math.sqrt(
                    sensorData[index].x * sensorData[index].x +
                    sensorData[index].y * sensorData[index].y +
                    sensorData[index].z * sensorData[index].z
                );
            }
        }

        // 双线性插值到 12×8 网格（3*4 = 12 行，2*4 = 8 列）
        const targetRows = 12;
        const targetCols = 8;
        const interpolated = [];

        for (let i = 0; i < targetRows; i++) {
            interpolated[i] = [];
            for (let j = 0; j < targetCols; j++) {
                // 映射回 3x2 网格中的浮点坐标
                const x = j / (targetCols / 2);      // 范围 [0, 2)
                const y = i / (targetRows / 3);      // 范围 [0, 3)

                const x1 = Math.floor(x);
                const y1 = Math.floor(y);
                const x2 = Math.min(x1 + 1, 1);     // 宽度只有2列 → 最大索引是1
                const y2 = Math.min(y1 + 1, 2);     // 高度只有3行 → 最大索引是2

                const wx = x - x1; // 水平权重
                const wy = y - y1; // 垂直权重

                // 获取四个角的值
                const q11 = zValues3x2[y1][x1];
                const q12 = zValues3x2[y2][x1];
                const q21 = zValues3x2[y1][x2];
                const q22 = zValues3x2[y2][x2];

                // 双线性插值
                const value =
                    q11 * (1 - wx) * (1 - wy) +
                    q21 * wx * (1 - wy) +
                    q12 * (1 - wx) * wy +
                    q22 * wx * wy;

                interpolated[i][j] = value;
            }
        }

        return interpolated;
    }, [sensorData]);

// 计算最小值和最大值用于颜色映射
    const { min, max } = useMemo(() => {
        if (!interpolatedData) return { min: 0, max: 1 };

        let minVal = Infinity;
        let maxVal = -Infinity;

        for (let i = 0; i < 12; i++) {
            for (let j = 0; j < 8; j++) {
                const v = interpolatedData[i][j];
                if (v < minVal) minVal = v;
                if (v > maxVal) maxVal = v;
            }
        }

        return { min: minVal, max: maxVal };
    }, [interpolatedData]);

    // jet 色谱：蓝→青→绿→黄→红，alpha 随压力深浅变化
    const valueToColor = (value) => {
        if (min === max || max === 0) return 'rgba(20,50,160,0.18)';

        // 相对位置决定色相（哪里压力集中）
        const n = Math.max(0, Math.min(1, (value - min) / (max - min)));
        // 绝对强度决定透明度（力越大越亮）
        const alpha = Math.pow(Math.max(0, value / max), 0.5) * 0.92 + 0.08;

        let r, g, b;
        if (n < 0.25) {
            const f = n / 0.25;
            r = 0; g = Math.round(f * 255); b = 255;
        } else if (n < 0.5) {
            const f = (n - 0.25) / 0.25;
            r = 0; g = 255; b = Math.round(255 * (1 - f));
        } else if (n < 0.75) {
            const f = (n - 0.5) / 0.25;
            r = Math.round(f * 255); g = 255; b = 0;
        } else {
            const f = (n - 0.75) / 0.25;
            r = 255; g = Math.round(255 * (1 - f)); b = 0;
        }
        return `rgba(${r},${g},${b},${alpha.toFixed(2)})`;
    };

    return (
        <div className={embedded ? 'sensor-grid sensor-grid--embedded' : compact ? 'sensor-grid sensor-grid--compact' : 'sensor-grid'}>
            {!compact && !embedded && <h2>传感器阵列 (3×2 → 12×8)</h2>}
            <div className="grid-container">
                {interpolatedData.map((row, i) => (
                    <div key={i} className="grid-row">
                        {row.map((value, j) => (
                            <div
                                key={j}
                                className="grid-cell"
                                style={{ backgroundColor: valueToColor(value) }}
                                title={`值: ${value.toFixed(2)}`}
                            />
                        ))}
                    </div>
                ))}
            </div>
        </div>
    );
};

export default SensorGrid;