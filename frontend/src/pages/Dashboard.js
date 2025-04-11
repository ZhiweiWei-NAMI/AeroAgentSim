import React, { useState, useEffect } from 'react';
import { Row, Col, Card, Statistic, Table, Timeline, Badge, Spin, Alert, Button } from 'antd';
import {
  RocketOutlined,
  ThunderboltOutlined,
  ClockCircleOutlined,
  AimOutlined,
  TeamOutlined,
  GlobalOutlined
} from '@ant-design/icons';
import { Link } from 'react-router-dom';
import { droneApi, workflowApi, agentApi, connectWebSocket } from '../services/api';

const Dashboard = () => {
  // 状态管理
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [drones, setDrones] = useState([]);
  const [workflows, setWorkflows] = useState([]);
  const [agents, setAgents] = useState([]);
  const [events, setEvents] = useState([]);
  const [simulationStatus, setSimulationStatus] = useState({
    status: 'STOPPED',
    time: 0,
    speed: 1.0
  });
  const [webSocket, setWebSocket] = useState(null);

  // 初始化WebSocket连接
  useEffect(() => {
    const ws = connectWebSocket(
      // 消息处理函数
      (data) => {
        if (data.type === 'sim_status') {
          setSimulationStatus({
            status: data.status,
            time: data.time,
            speed: data.speed
          });
        } else if (data.type === 'sim_event') {
          setEvents(prev => {
            const newEvents = [
              {
                id: Date.now(),
                time: data.time,
                source: data.source,
                message: data.message,
                level: data.level
              },
              ...prev
            ];
            // 保留最近的20条事件
            return newEvents.slice(0, 20);
          });
        } else if (data.type === 'drone_update') {
          // 更新无人机状态
          setDrones(prev => {
            const updatedDrones = [...prev];
            const index = updatedDrones.findIndex(d => d.id === data.drone.id);
            if (index >= 0) {
              updatedDrones[index] = data.drone;
            } else {
              updatedDrones.push(data.drone);
            }
            return updatedDrones;
          });
        }
      },
      // 连接打开回调
      () => {
        console.log('WebSocket连接已建立');
        setError(null);
      },
      // 连接关闭回调
      () => {
        console.log('WebSocket连接已关闭');
      },
      // 错误回调
      (error) => {
        console.error('WebSocket错误:', error);
        setError('WebSocket连接出错，部分实时数据可能无法更新');
      }
    );

    setWebSocket(ws);

    // 组件卸载时关闭WebSocket连接
    return () => {
      if (ws) {
        ws.close();
      }
    };
  }, []);

  // 加载初始数据
  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      setError(null);
      try {
        // 并行请求数据
        const [dronesData, workflowsData, agentsData] = await Promise.all([
          droneApi.getAllDrones(),
          workflowApi.getAllWorkflows(),
          agentApi.getAllAgents()
        ]);

        setDrones(dronesData);
        setWorkflows(workflowsData);
        setAgents(agentsData);
      } catch (err) {
        console.error('获取数据失败:', err);
        setError('获取数据失败，请检查网络连接或刷新页面重试');
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, []);

  // 无人机状态表格列定义
  const droneColumns = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 100,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status) => {
        let color = 'green';
        if (status === 'idle') color = 'blue';
        else if (status === 'charging') color = 'orange';
        else if (status === 'error') color = 'red';
        
        return <Badge status={color} text={status} />;
      }
    },
    {
      title: '电池电量',
      dataIndex: 'battery_level',
      key: 'battery_level',
      width: 120,
      render: (battery) => {
        let color = 'green';
        if (battery < 30) color = 'red';
        else if (battery < 60) color = 'orange';
        
        return (
          <div>
            <Badge color={color} /> {battery.toFixed(1)}%
          </div>
        );
      }
    },
    {
      title: '位置',
      dataIndex: 'position',
      key: 'position',
      render: (position) => {
        if (!position) return 'N/A';
        const pos = Array.isArray(position) ? position : JSON.parse(position);
        return `(${pos[0].toFixed(1)}, ${pos[1].toFixed(1)}, ${pos[2].toFixed(1)})`;
      }
    },
    {
      title: '速度',
      dataIndex: 'speed',
      key: 'speed',
      width: 100,
      render: (speed) => `${speed ? speed.toFixed(1) : '0.0'} m/s`
    }
  ];

  // 事件级别对应的图标
  const getEventIcon = (level) => {
    switch (level) {
      case 'success':
        return <Badge status="success" />;
      case 'warning':
        return <Badge status="warning" />;
      case 'error':
        return <Badge status="error" />;
      default:
        return <Badge status="processing" />;
    }
  };

  // 格式化仿真时间
  const formatSimTime = (time) => {
    const hours = Math.floor(time / 3600);
    const minutes = Math.floor((time % 3600) / 60);
    const seconds = Math.floor(time % 60);
    return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
  };

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: '50px' }}>
        <Spin size="large" tip="加载中..." />
      </div>
    );
  }

  return (
    <div className="dashboard">
      {error && (
        <Alert
          message="错误"
          description={error}
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          closable
        />
      )}

      {/* 状态统计卡片 */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card>
            <Statistic
              title="仿真状态"
              value={simulationStatus.status}
              valueStyle={{ 
                color: simulationStatus.status === 'RUNNING' ? '#3f8600' : 
                       simulationStatus.status === 'PAUSED' ? '#faad14' : '#cf1322' 
              }}
              prefix={<RocketOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="仿真时间"
              value={formatSimTime(simulationStatus.time)}
              prefix={<ClockCircleOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="活动无人机"
              value={drones.length}
              prefix={<ThunderboltOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="活动工作流"
              value={workflows.length}
              prefix={<AimOutlined />}
            />
          </Card>
        </Col>
      </Row>

      {/* 无人机状态表格 */}
      <Card 
        title="无人机状态" 
        style={{ marginBottom: 16 }}
        extra={<a href="/drones">查看详情</a>}
      >
        <Table 
          dataSource={drones} 
          columns={droneColumns} 
          rowKey="id" 
          pagination={false}
          size="small"
          scroll={{ y: 240 }}
        />
      </Card>

      {/* 事件时间线 */}
      <Row gutter={16}>
        <Col span={12}>
          <Card 
            title="系统事件" 
            style={{ marginBottom: 16 }}
            styles={{ body: { height: 300, overflowY: 'auto' } }}
          >
            <Timeline mode="left">
              {events.map(event => (
                <Timeline.Item 
                  key={event.id} 
                  dot={getEventIcon(event.level)}
                  label={formatSimTime(event.time)}
                >
                  <strong>{event.source}:</strong> {event.message}
                </Timeline.Item>
              ))}
              {events.length === 0 && (
                <div style={{ textAlign: 'center', color: '#999', padding: '20px' }}>
                  暂无事件记录
                </div>
              )}
            </Timeline>
          </Card>
        </Col>
        <Col span={12}>
          <Card 
            title="智能体状态" 
            style={{ marginBottom: 16 }}
            extra={<a href="/agents">查看详情</a>}
            styles={{ body: { height: 300, overflowY: 'auto' } }}
          >
            {agents.map(agent => (
              <Card.Grid key={agent.id} style={{ width: '50%', textAlign: 'center' }}>
                <TeamOutlined style={{ fontSize: 24, marginBottom: 8 }} />
                <p><strong>{agent.name}</strong></p>
                <p>类型: {agent.type}</p>
                <p>状态: {agent.properties?.status || 'N/A'}</p>
              </Card.Grid>
            ))}
            {agents.length === 0 && (
              <div style={{ textAlign: 'center', color: '#999', padding: '20px' }}>
                暂无智能体
              </div>
            )}
          </Card>
        </Col>
      </Row>
      {/* 3D地图视图入口 */}
      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col span={24}>
          <Card
            title={
              <span>
                <GlobalOutlined /> 3D地图视图
              </span>
            }
            extra={<Link to="/3dmap">查看详情</Link>}
          >
            <div style={{ textAlign: 'center', padding: '40px 0' }}>
              <p style={{ fontSize: '16px', marginBottom: '20px' }}>
                在3D地图视图中可以查看无人机、车辆位置和气象信息的实时状态
              </p>
              <Link to="/3dmap">
                <Button type="primary" icon={<GlobalOutlined />} size="large">
                  打开3D地图视图
                </Button>
              </Link>
            </div>
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default Dashboard;