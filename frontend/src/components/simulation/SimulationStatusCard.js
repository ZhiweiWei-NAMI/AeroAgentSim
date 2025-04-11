import React from 'react';
import { Card, Statistic, Divider, Form, Row, Col, Slider, InputNumber } from 'antd';
import { PlayCircleOutlined, PauseCircleOutlined, ReloadOutlined, ClockCircleOutlined } from '@ant-design/icons';
import { useSimulation, formatSimTime } from '../../contexts/SimulationContext';

// 仿真状态卡片组件
const SimulationStatusCard = () => {
  const { 
    simulationStatus, 
    simulationTime, 
    simulationSpeed, 
    handleSpeedChange 
  } = useSimulation();

  return (
    <Card title="仿真状态" className="status-card">
      <Statistic
        title="当前状态"
        value={simulationStatus}
        valueStyle={{ 
          color: simulationStatus === 'RUNNING' ? '#52c41a' : 
                  simulationStatus === 'PAUSED' ? '#faad14' : '#f5222d' 
        }}
        prefix={
          simulationStatus === 'RUNNING' ? <PlayCircleOutlined /> : 
          simulationStatus === 'PAUSED' ? <PauseCircleOutlined /> : <ReloadOutlined />
        }
      />
      
      <Divider />
      
      <Statistic
        title="仿真时间"
        value={formatSimTime(simulationTime)}
        prefix={<ClockCircleOutlined />}
      />
      
      <Divider />
      
      <div>
        <p>仿真速度: {simulationSpeed}x</p>
        <Form.Item>
          <Row>
            <Col span={18}>
              <Slider
                min={0.1}
                max={10}
                step={0.1}
                value={simulationSpeed}
                onChange={handleSpeedChange}
              />
            </Col>
            <Col span={4} offset={2}>
              <InputNumber
                min={0.1}
                max={10}
                step={0.1}
                value={simulationSpeed}
                onChange={handleSpeedChange}
                style={{ width: '100%' }}
              />
            </Col>
          </Row>
        </Form.Item>
      </div>
    </Card>
  );
};

export default SimulationStatusCard;