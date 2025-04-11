import React from 'react';
import { Card, Form, InputNumber } from 'antd';
import { useSimulation } from '../../contexts/SimulationContext';

// 资源参数卡片组件
const SimulationResourceCard = () => {
  const { envParams, handleEnvParamChange } = useSimulation();

  return (
    <Card type="inner" title="资源参数">
      <Form layout="vertical">
        <Form.Item label="CPU资源">
          <InputNumber
            addonAfter="核心"
            style={{ width: 150 }}
            value={envParams.cpuResources}
            onChange={(value) => handleEnvParamChange('cpuResources', value)}
            min={1}
          />
        </Form.Item>
        
        <Form.Item label="内存资源">
          <InputNumber
            addonAfter="GB"
            style={{ width: 150 }}
            value={envParams.memoryResources}
            onChange={(value) => handleEnvParamChange('memoryResources', value)}
            min={1}
          />
        </Form.Item>
        
        <Form.Item label="带宽限制">
          <InputNumber
            addonAfter="Mbps"
            style={{ width: 150 }}
            value={envParams.bandwidthLimit}
            onChange={(value) => handleEnvParamChange('bandwidthLimit', value)}
            min={1}
          />
        </Form.Item>
      </Form>
    </Card>
  );
};

export default SimulationResourceCard;