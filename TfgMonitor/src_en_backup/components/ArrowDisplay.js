import React from 'react';
import '../styles/VectorMagnitudeDisplay.css';

const VectorMagnitudeDisplay = ({ x, y, z }) => {
    // 计算向量长度
    const magnitude = Math.sqrt(x * x + y * y + z * z);

    // 计算颜色映射（从蓝色到红色）
    const normalizedMagnitude = Math.min(magnitude / 800, 1); // 限制最大值为20进行归一化
    const hue = 240 * (1 - normalizedMagnitude);
    const color = `hsl(${hue}, 100%, 50%)`;

    return (
        <div className="magnitude-display"
             style={{
                 backgroundColor: color}}
        >
            {magnitude.toFixed(2)}
        </div>
    );
};

export default VectorMagnitudeDisplay;