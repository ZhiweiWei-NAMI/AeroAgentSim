import React from 'react';
import { Card, Row, Col, Space, Button } from 'antd';
import { SettingOutlined } from '@ant-design/icons';
import { useSimulation } from '../../contexts/SimulationContext';
import SimulationMapConfigCard from './SimulationMapConfigCard';
import SimulationTrafficCard from './SimulationTrafficCard';
import SimulationAdvancedCard from './SimulationAdvancedCard';

// 仿真参数面板组件
const SimulationParamsPanel = () => {
  const { handleApplySettings, loading } = useSimulation();

  return (
    <Card title="仿真参数配置" style={{ marginTop: 16 }}>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={24}>
          <SimulationMapConfigCard />
        </Col>
      </Row>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={12}>
          <SimulationTrafficCard />
        </Col>
        {/* <Col span={12}>
          <SimulationTrafficCard />
        </Col> */}
      </Row>
      <Row gutter={16}>
        <Col span={24}>
          <SimulationAdvancedCard />
        </Col>
      </Row>
      
      <div style={{ marginTop: 16, textAlign: 'center' }}>
        <Space>
          <Button
            type="primary"
            icon={<SettingOutlined />}
            onClick={handleApplySettings}
            loading={loading}
          >
            应用设置
          </Button>
          <Button>
            恢复默认
          </Button>
        </Space>
      </div>
    </Card>
  );
};

export default SimulationParamsPanel;