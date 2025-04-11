import React from 'react';
import { Card, Button, Timeline } from 'antd';
import { 
  ReloadOutlined, 
  CheckCircleOutlined, 
  CloseCircleOutlined, 
  InfoCircleOutlined, 
  ClockCircleOutlined 
} from '@ant-design/icons';
import { useSimulation, formatSimTime } from '../../contexts/SimulationContext';

// 事件日志卡片组件
const SimulationEventsCard = () => {
  const { events, clearEvents } = useSimulation();

  // 获取事件图标
  const getEventIcon = (level) => {
    switch (level) {
      case 'success':
        return <CheckCircleOutlined style={{ color: '#52c41a' }} />;
      case 'error':
        return <CloseCircleOutlined style={{ color: '#f5222d' }} />;
      case 'warning':
        return <InfoCircleOutlined style={{ color: '#faad14' }} />;
      default:
        return <ClockCircleOutlined style={{ color: '#1890ff' }} />;
    }
  };

  return (
    <Card 
      title="事件日志" 
      className="events-card"
      extra={
        <Button 
          type="text" 
          icon={<ReloadOutlined />} 
          onClick={clearEvents}
        >
          清空
        </Button>
      }
    >
      <div className="events-timeline" style={{ maxHeight: 400, overflowY: 'auto' }}>
        {events.length > 0 ? (
          <Timeline mode="left">
            {events.map(event => (
              <Timeline.Item 
                key={event.id} 
                dot={getEventIcon(event.level)}
                label={formatSimTime(event.time)}
              >
                <p><strong>{event.source}</strong></p>
                <p>{event.message}</p>
              </Timeline.Item>
            ))}
          </Timeline>
        ) : (
          <div style={{ textAlign: 'center', padding: 20 }}>
            <p>暂无事件</p>
          </div>
        )}
      </div>
    </Card>
  );
};

export default SimulationEventsCard;