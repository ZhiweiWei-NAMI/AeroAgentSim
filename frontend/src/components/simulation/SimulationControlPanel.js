import React from 'react';
import { Row, Col, Alert, Spin } from 'antd';
import { useSimulation } from '../../contexts/SimulationContext';
import SimulationStatusCard from './SimulationStatusCard';
import SimulationControlCard from './SimulationControlCard';
import SimulationEventsCard from './SimulationEventsCard';
import SimulationParamsPanel from './SimulationParamsPanel';

// 仿真控制面板主组件
const SimulationControlPanel = () => {
  const { connected, error, loading } = useSimulation();

  return (
    <div className="simulation-control-container">
      <h2>仿真控制</h2>
      
      {!connected && (
        <Alert 
          message="WebSocket未连接" 
          description="实时更新功能不可用，请刷新页面重试" 
          type="warning" 
          showIcon 
          style={{ marginBottom: 16 }}
        />
      )}
      
      {error && (
        <Alert 
          message="操作失败" 
          description={error} 
          type="error" 
          showIcon 
          style={{ marginBottom: 16 }}
        />
      )}
      
      <Spin spinning={loading}>
        <Row gutter={16}>
          {/* 仿真状态卡片 */}
          <Col span={8}>
            <SimulationStatusCard />
          </Col>
          
          {/* 控制按钮卡片 */}
          <Col span={8}>
            <SimulationControlCard />
          </Col>
          
          {/* 事件日志卡片 */}
          <Col span={8}>
            <SimulationEventsCard />
          </Col>
        </Row>
        
        {/* 仿真参数面板 */}
        <SimulationParamsPanel />
      </Spin>
    </div>
  );
};

export default SimulationControlPanel;