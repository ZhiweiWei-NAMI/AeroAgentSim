import React from 'react';
import { Card, Form, InputNumber, Select } from 'antd';
import { useSimulation } from '../../contexts/SimulationContext';

const { Option } = Select;

// 高级设置卡片组件
const SimulationAdvancedCard = () => {
  const { envParams, handleEnvParamChange } = useSimulation();

  return (
    <Card type="inner" title="高级设置">
      <Form layout="vertical">
        <Form.Item label="随机种子">
          <InputNumber
            style={{ width: 150 }}
            value={envParams.randomSeed}
            onChange={(value) => handleEnvParamChange('randomSeed', value)}
          />
        </Form.Item>
        
        <Form.Item label="日志级别">
          <Select
            value={envParams.logLevel}
            onChange={(value) => handleEnvParamChange('logLevel', value)}
            style={{ width: '100%' }}
          >
            <Option value="debug">调试</Option>
            <Option value="info">信息</Option>
            <Option value="warning">警告</Option>
            <Option value="error">错误</Option>
          </Select>
        </Form.Item>
        
        <Form.Item label="最大仿真时间">
          <InputNumber
            addonAfter="秒"
            style={{ width: 150 }}
            value={envParams.maxSimTime}
            onChange={(value) => handleEnvParamChange('maxSimTime', value)}
            min={1}
          />
        </Form.Item>
      </Form>
    </Card>
  );
};

export default SimulationAdvancedCard;