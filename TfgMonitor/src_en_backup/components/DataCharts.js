import React, { useState, useEffect, useRef, useCallback } from 'react';
import * as echarts from 'echarts';

const DataCharts = ({ averages }) => {
    const chartRefX = useRef(null);
    const chartRefY = useRef(null);
    const chartRefZ = useRef(null);
    const chartInstanceX = useRef(null);
    const chartInstanceY = useRef(null);
    const chartInstanceZ = useRef(null);
    const dataHistory = useRef([]);
    const requestRef = useRef(null);

    // 初始化图表
    const initChart = useCallback((chartInstance, axisName, color) => {
        if (!chartInstance) return;

        const option = {
            grid: {
                left: '3%',
                right: '4%',
                bottom: '3%',
                top: '8%',
                containLabel: true
            },
            xAxis: {
                type: 'value', // 改为value类型
                boundaryGap: false,
                axisLine: {
                    lineStyle: {
                        color: '#e0e0ff'
                    }
                },
                axisLabel: {
                    color: '#e0e0ff',
                    fontSize: 12
                },
                min: 0, // 设置最小值
                max: 49 // 设置最大值（因为我们只保留50个数据点）
            },
            yAxis: {
                type: 'value',
                axisLine: {
                    lineStyle: {
                        color: '#e0e0ff'
                    }
                },
                axisLabel: {
                    color: '#e0e0ff'
                },
                splitLine: {
                    lineStyle: {
                        color: '#2a2a4a',
                        type: 'dashed'
                    }
                },
                min: -2000,
                max: 2000
            },
            tooltip: {
                trigger: 'axis',
                backgroundColor: 'rgba(30, 35, 60, 0.9)',
                borderColor: '#4a5585',
                textStyle: {
                    color: '#e0e0ff'
                },
                axisPointer: {
                    type: 'line',
                    lineStyle: {
                        color: '#4a5585'
                    }
                }
            },
            series: [
                {
                    name: `${axisName}轴数据`,
                    type: 'line',
                    data: [],
                    smooth: true,
                    symbol: 'none',
                    lineStyle: {
                        color: color,
                        width: 2
                    }
                }
            ]
        };

        chartInstance.setOption(option);
    }, []);

    // 更新图表数据
    const updateCharts = useCallback(() => {
        if (!chartInstanceX.current || !chartInstanceY.current || !chartInstanceZ.current) return;

        // 准备数据点
        const dataPoints = dataHistory.current.map((item, index) => [index, item.x]);
        const dataPointsY = dataHistory.current.map((item, index) => [index, item.y]);
        const dataPointsZ = dataHistory.current.map((item, index) => [index, item.z]);

        // 只更新数据部分，避免重渲染整个图表
        chartInstanceX.current.setOption({
            series: [
                { data: dataPoints}
            ],
            yAxis: {
                max:500,
                min:-500 }
        });

        chartInstanceY.current.setOption({
            series: [
                { data: dataPointsY}
            ],
            yAxis: {
            max:500,
            min:-500 }

        });

        chartInstanceZ.current.setOption({
            series: [
                { data: dataPointsZ}
            ],
            yAxis: {
                max:5000,
                min:-500 }
        });
    }, []);

    // 处理数据更新
    const processData = useCallback(() => {
        if (
            typeof averages.x !== 'number' ||
            typeof averages.y !== 'number' ||
            typeof averages.z !== 'number'
        ) {
            return;
        }

        const newDataPoint = {
            x: averages.x,
            y: averages.y,
            z: averages.z
        };

        dataHistory.current = [...dataHistory.current, newDataPoint];

        // 限制数据历史长度
        if (dataHistory.current.length > 50) {
            dataHistory.current = dataHistory.current.slice(-50);
        }

        // 使用requestAnimationFrame进行平滑更新
        requestRef.current = requestAnimationFrame(updateCharts);
    }, [averages, updateCharts]);

    // 初始化和清理
    useEffect(() => {
        // 初始化图表实例
        if (chartRefX.current) {
            chartInstanceX.current = echarts.init(chartRefX.current);
            initChart(chartInstanceX.current, 'X', '#4a9eff');
        }

        if (chartRefY.current) {
            chartInstanceY.current = echarts.init(chartRefY.current);
            initChart(chartInstanceY.current, 'Y', '#6c5ce7');
        }

        if (chartRefZ.current) {
            chartInstanceZ.current = echarts.init(chartRefZ.current);
            initChart(chartInstanceZ.current, 'Z', '#00cec9');
        }

        // 处理窗口大小变化
        const handleResize = () => {
            if (chartInstanceX.current) {
                chartInstanceX.current.resize();
            }
            if (chartInstanceY.current) {
                chartInstanceY.current.resize();
            }
            if (chartInstanceZ.current) {
                chartInstanceZ.current.resize();
            }
        };

        window.addEventListener('resize', handleResize);

        // 清理函数
        return () => {
            window.removeEventListener('resize', handleResize);

            if (requestRef.current) {
                cancelAnimationFrame(requestRef.current);
            }

            if (chartInstanceX.current) {
                chartInstanceX.current.dispose();
            }

            if (chartInstanceY.current) {
                chartInstanceY.current.dispose();
            }

            if (chartInstanceZ.current) {
                chartInstanceZ.current.dispose();
            }
        };
    }, [initChart]);

    // 处理数据更新
    useEffect(() => {
        processData();
    }, [averages, processData]);

    return (
        <div className="data-charts">
            <h2>X-Y-Z轴力实时数据图表</h2>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
                <div
                    ref={chartRefX}
                    style={{ width: '100%', height: '100px' }}
                />
                <div
                    ref={chartRefY}
                    style={{ width: '100%', height: '100px' }}
                />
                <div
                    ref={chartRefZ}
                    style={{ width: '100%', height: '100px' }}
                />
            </div>
        </div>
    );
};

export default DataCharts;