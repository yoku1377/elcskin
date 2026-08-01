const data_length = 18;
class DataParser {
    constructor() {
        this.buffer = ''; // 字符串缓冲区
    }

    /**
     * 输入原始字符串数据，尝试提取完整帧（以 \n 结尾）
     * 支持跨包拼接（如半条数据在上一个包中）
     * @param {string} chunk 原始串口数据片段
     * @returns {number[]|null} 成功解析则返回长度为48的数组，否则 null
     */
    parse(chunk) {


        this.buffer += chunk;

        // 查找所有以 \n 结尾的完整行
        const lines = this.buffer.split('\n');

        // 最后一行可能是不完整的（等待下一次数据），保留在 buffer 中
        this.buffer = lines[lines.length - 1];

        // 处理所有完整的行（除了最后一行）
        for (let i = 0; i < lines.length - 1; i++) {
            const line = lines[i].trim();
            if (line === '') continue;

            const parsed = this._parseLine(line);
            if (parsed) {
                return parsed; // 返回第一个有效帧（也可改为 emit 所有帧）
            }
        }

        return null; // 暂无有效完整帧
    }

    /**
     * 解析单行数据：应包含 48 个用逗号分隔的数字
     * @param {string} line
     * @returns {number[]|null}
     */
    _parseLine(line) {
        const values = line.split(',')
            .map(s => parseFloat(s.trim()))
            .filter(x => !isNaN(x)); // 过滤掉 NaN
        if (values.length === data_length) {
            return values; // [Bx1, By1, Bz1, ..., Bz16]
        }

        console.warn('The data length does not match, expected ',data_length,', actual:', values.length, 'data:', line);
        return null;
    }

    clear() {
        this.buffer = '';
    }

    getBuffer() {
        return this.buffer;
    }
}

module.exports = { DataParser };