import React from 'react';
import { Card, Button, Space, Divider, Descriptions, Switch } from 'antd';
import { PlayCircleOutlined, PauseCircleOutlined, ReloadOutlined } from '@ant-design/icons';
import { useSimulation } from '../../contexts/SimulationContext';

// 仿真控制卡片组件
const SimulationControlCard = () => {
  const { 
    simulationStatus, 
    handleStartSimulation, 
    handlePauseSimulation, 
    handleResumeSimulation, 
    handleResetSimulation 
  } = useSimulation();

  return (
    <Card title="仿真控制" className="control-card">
      <Space direction="vertical" style={{ width: '100%' }}>
        <Button 
          type="primary" 
          icon={<PlayCircleOutlined />} 
          size="large" 
          block
          onClick={handleStartSimulation}
          disabled={simulationStatus === 'RUNNING'}
        >
          启动仿真
        </Button>
        
        <Button 
          type="default" 
          icon={<PauseCircleOutlined />} 
          size="large" 
          block
          onClick={handlePauseSimulation}
          disabled={simulationStatus !== 'RUNNING'}
        >
          暂停仿真
        </Button>
        
        <Button 
          type="default" 
          icon={<PlayCircleOutlined />} 
          size="large" 
          block
          onClick={handleResumeSimulation}
          disabled={simulationStatus !== 'PAUSED'}
        >
          恢复仿真
        </Button>
        
        <Button 
          type="danger" 
          icon={<ReloadOutlined />} 
          size="large" 
          block
          onClick={handleResetSimulation}
          disabled={simulationStatus === 'STOPPED'}
        >
          重置仿真
        </Button>
      </Space>
      
      <Divider />
      
      <Descriptions title="仿真设置" column={1} size="small">
        <Descriptions.Item label="实时可视化">
          <Switch defaultChecked />
        </Descriptions.Item>
        <Descriptions.Item label="记录事件">
          <Switch defaultChecked />
        </Descriptions.Item>
        <Descriptions.Item label="调试模式">
          <Switch />
        </Descriptions.Item>
      </Descriptions>
    </Card>
  );
};

export default SimulationControlCard;