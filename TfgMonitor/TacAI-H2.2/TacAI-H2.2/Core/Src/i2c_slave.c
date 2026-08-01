/**
 * @file i2c_slave.c
 * @author ultralyj (yijie_luo@tongji.edu.cn)
 * @brief
 * @version 0.1
 * @date 2025-10-09
 *
 * @copyright Copyright (c) 2025
 *
 */

#ifdef __cplusplus
extern "C"
{
#endif

/* Includes ------------------------------------------------------------------*/
#include "i2c_slave.h"
#include "log.h"

    uint8_t g_i2c_reg_ram_buf0[I2C_REG_RAM_SIZE]; // 缓冲区0
    uint8_t g_i2c_reg_ram_buf1[I2C_REG_RAM_SIZE]; // 缓冲区1
    // 当前用于I2C传输的缓冲区指针（前台缓冲区）
    volatile uint8_t *g_i2c_active_ram = g_i2c_reg_ram_buf0;
    // 当前用于主循环写入的缓冲区指针（后台缓冲区）
    uint8_t *g_i2c_write_ram = g_i2c_reg_ram_buf1;
    // 用于交换缓冲区的锁，防止在交换时发生中断
    volatile uint8_t buffer_swap_lock = 0;

    static uint8_t g_i2c_slave_offset;       // 从机寄存器当前偏移地址
    static I2C_SlaveState g_i2c_slave_state = I2C_STATE_WAITING_FOR_OFFSET;

    void HAL_I2C_ListenCpltCallback(I2C_HandleTypeDef *hi2c)
    {
        /* 重新开始监听时重置状态 */
        g_i2c_slave_state = I2C_STATE_WAITING_FOR_OFFSET;
        g_i2c_slave_offset = 0;

        HAL_I2C_EnableListen_IT(hi2c); // 重新使能从机监听
    }

    void HAL_I2C_AddrCallback(I2C_HandleTypeDef *hi2c, uint8_t TransferDirection, uint16_t AddrMatchCode)
    {
        if (TransferDirection == I2C_DIRECTION_TRANSMIT)
        {
            /* 主机写入数据到从机 */
            if (g_i2c_slave_state == I2C_STATE_WAITING_FOR_OFFSET)
            {
                // 接收地址偏移量字节
                HAL_I2C_Slave_Seq_Receive_IT(hi2c, &g_i2c_slave_offset, 1, I2C_NEXT_FRAME);
            }
        }
        else
        {
            /* 主机从从机读取数据 */
            HAL_I2C_Slave_Seq_Transmit_IT(hi2c, (uint8_t*)&g_i2c_active_ram[g_i2c_slave_offset], 1, I2C_NEXT_FRAME);
        }
    }

    void HAL_I2C_SlaveRxCpltCallback(I2C_HandleTypeDef *hi2c)
    {
        if (g_i2c_slave_state == I2C_STATE_WAITING_FOR_OFFSET)
        {
            /* 已完成地址偏移量接收，切换至数据处理状态 */
            g_i2c_slave_state = I2C_STATE_PROCESSING_DATA;
            
            uint8_t *temp_ptr = (uint8_t*)g_i2c_active_ram;
            g_i2c_active_ram = g_i2c_write_ram;
            g_i2c_write_ram = temp_ptr;
        }
        else
        {
            /* 正常数据接收，更新偏移量 */
            g_i2c_slave_offset++;
        }

        /* 准备接收下一个数据字节 */
        HAL_I2C_Slave_Seq_Receive_IT(hi2c, (uint8_t*)&g_i2c_active_ram[g_i2c_slave_offset], 1, I2C_NEXT_FRAME);
    }

    void HAL_I2C_SlaveTxCpltCallback(I2C_HandleTypeDef *hi2c)
    {
        /* 更新发送偏移量并准备下一个数据 */
        g_i2c_slave_offset++;
        HAL_I2C_Slave_Seq_Transmit_IT(hi2c, (uint8_t*)&g_i2c_active_ram[g_i2c_slave_offset], 1, I2C_NEXT_FRAME);
    }

    void HAL_I2C_ErrorCallback(I2C_HandleTypeDef *hi2c)
    {
        /* 清除错误标志并重新使能监听 */
        __HAL_I2C_CLEAR_FLAG(hi2c, I2C_FLAG_AF | I2C_FLAG_ARLO | I2C_FLAG_BERR | I2C_FLAG_OVR);
        HAL_I2C_EnableListen_IT(hi2c);
    }

#ifdef __cplusplus
}
#endif
