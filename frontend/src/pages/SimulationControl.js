import React, { useState, useEffect, useRef } from 'react';
import {
  Card, Button, Row, Col, Statistic, Alert,
  Divider, Timeline, Space, Descriptions, Switch,
  Slider, InputNumber, Form, Spin, Select
} from 'antd';

import { 
  PlayCircleOutlined, PauseCircleOutlined, 
  ReloadOutlined, ClockCircleOutlined,
  CheckCircleOutlined, CloseCircleOutlined,
  SettingOutlined, InfoCircleOutlined
} from '@ant-design/icons';
import { simulationApi, connectWebSocket, sendWebSocketMessage, agentApi, workflowApi } from '../services/api';

const { Option } = Select;
const SimulationControl = () => {
  const [simulationStatus, setSimulationStatus] = useState('STOPPED');
  const [simulationTime, setSimulationTime] = useState(0);
  const [simulationSpeed, setSimulationSpeed] = useState(1);
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [connected, setConnected] = useState(false);
  const webSocketRef = useRef(null);
  const [agents, setAgents] = useState([]);
  const [workflows, setWorkflows] = useState([]);
  const [envParams, setEnvParams] = useState({
    mapWidth: 1000,
    mapHeight: 1000,
    obstaclesDensity: 30,
    weather: 'sunny',
    cpuResources: 4,
    memoryResources: 8,
    bandwidthLimit: 100,
    randomSeed: 42,
    logLevel: 'info',
    maxSimTime: 3600
  });
  
  // 加载智能体和工作流数据
  useEffect(() => {
    const fetchData = async () => {
      try {
        const [agentsData, workflowsData] = await Promise.all([
          agentApi.getAllAgents(),
          workflowApi.getAllWorkflows()
        ]);
        
        setAgents(agentsData.data || agentsData);
        setWorkflows(workflowsData.data || workflowsData);
      } catch (err) {
        console.error('获取数据失败:', err);
        setError('获取智能体和工作流数据失败，部分功能可能受限');
      }
    };
    
    fetchData();
  }, []);

  // 初始化WebSocket连接
  useEffect(() => {
    const onOpen = () => {
      setConnected(true);
      setError(null);
    };
    
    const onClose = () => {
      setConnected(false);
    };
    
    const onError = (err) => {
      setConnected(false);
      setError('WebSocket连接失败，部分功能可能不可用');
      console.error('WebSocket错误:', err);
    };
    const onMessage = (data) => {
      if (data.type === 'sim_status') {
        setSimulationStatus(data.status);
        setSimulationTime(data.time);
        setSimulationSpeed(data.speed || 1);
      } else if (data.type === 'sim_event') {
        // 添加新事件
        setEvents(prevEvents => {
          const newEvents = [...prevEvents, {
            id: Date.now(),
            time: data.time,
            source: data.source,
            message: data.message,
            level: data.level || 'info',
            timestamp: new Date().toISOString()
          }];
          
          // 保持最新的20条事件
          return newEvents.slice(-20);
        });
      }
    };
    
    const socket = connectWebSocket(onMessage, onOpen, onClose, onError);
    webSocketRef.current = socket;
    
    // 清理函数
    return () => {
      if (webSocketRef.current) {
        webSocketRef.current.close();
      }
    };
  }, []);
  
  // 启动仿真
  const handleStartSimulation = async () => {
    setLoading(true);
    setError(null);
    try {
      await simulationApi.startSimulation();
      
      // 通过WebSocket发送命令
      if (webSocketRef.current) {
        sendWebSocketMessage(webSocketRef.current, 'sim_control', 'start');
      }
      
      setSimulationStatus('RUNNING');
    } catch (err) {
      console.error('启动仿真失败:', err);
      setError('启动仿真失败，请稍后重试');
    } finally {
      setLoading(false);
    }
  };
  
  // 暂停仿真
  const handlePauseSimulation = async () => {
    setLoading(true);
    setError(null);
    try {
      await simulationApi.pauseSimulation();
      
      // 通过WebSocket发送命令
      if (webSocketRef.current) {
        sendWebSocketMessage(webSocketRef.current, 'sim_control', 'pause');
      }
      
      setSimulationStatus('PAUSED');
    } catch (err) {
      console.error('暂停仿真失败:', err);
      setError('暂停仿真失败，请稍后重试');
    } finally {
      setLoading(false);
    }
  };
  
  // 恢复仿真
  const handleResumeSimulation = async () => {
    setLoading(true);
    setError(null);
    try {
      await simulationApi.resumeSimulation();
      
      // 通过WebSocket发送命令
      if (webSocketRef.current) {
        sendWebSocketMessage(webSocketRef.current, 'sim_control', 'resume');
      }
      
      setSimulationStatus('RUNNING');
    } catch (err) {
      console.error('恢复仿真失败:', err);
      setError('恢复仿真失败，请稍后重试');
    } finally {
      setLoading(false);
    }
  };
  
  // 重置仿真
  const handleResetSimulation = async () => {
    setLoading(true);
    setError(null);
    try {
      await simulationApi.resetSimulation();
      
      // 通过WebSocket发送命令
      if (webSocketRef.current) {
        sendWebSocketMessage(webSocketRef.current, 'sim_control', 'reset');
      }
      
      setSimulationStatus('STOPPED');
      setSimulationTime(0);
      setEvents([]);
    } catch (err) {
      console.error('重置仿真失败:', err);
      setError('重置仿真失败，请稍后重试');
    } finally {
      setLoading(false);
    }
  };
  
  // 更改仿真速度
  const handleSpeedChange = (value) => {
    setSimulationSpeed(value);
    
    // 通过WebSocket发送命令
    if (webSocketRef.current) {
      sendWebSocketMessage(webSocketRef.current, 'sim_control', 'set_speed', {
        speed: value
      });
    }
  };
  
  // 处理环境参数变化
  const handleEnvParamChange = (key, value) => {
    setEnvParams(prev => ({
      ...prev,
      [key]: value
    }));
  };
  
  // 应用仿真配置
  const handleApplySettings = async () => {
    setLoading(true);
    setError(null);
    try {
      // 构建配置对象
      const config = {
        airspaces: [{
          x_range: [0, envParams.mapWidth],
          y_range: [0, envParams.mapHeight],
          altitude_range: [0, 100],
          max_capacity: 100,
          attributes: {
            weather: envParams.weather,
            obstacles_density: envParams.obstaclesDensity
          }
        }],
        frequencies: [{
          frequency_range: [2400, 2500],
          bandwidth: envParams.bandwidthLimit,
          max_users: 50,
          power_limit: 20,
          attributes: {}
        }],
        landing_spots: [],
        agents: agents.map(agent => ({
          id: agent.id,
          name: agent.name,
          type: agent.type,
          position: agent.position || [0, 0, 0],
          properties: agent.properties || {}
        })),
        workflows: workflows.map(workflow => ({
          id: workflow.id,
          name: workflow.name,
          type: workflow.type,
          agent_id: workflow.agent_id,
          details: workflow.details || {}
        })),
        simulation_time: 0,
        simulation_speed: simulationSpeed
      };
      
      // 调用配置API
      await simulationApi.configureSimulation(config);
      
      // 发送WebSocket消息
      if (webSocketRef.current) {
        sendWebSocketMessage(webSocketRef.current, 'sim_config', null, { config });
      }
      
      // 显示成功消息
      setEvents(prev => {
        const newEvents = [...prev, {
          id: Date.now(),
          time: simulationTime,
          source: "系统",
          message: "仿真配置已应用",
          level: "success",
          timestamp: new Date().toISOString()
        }];
        return newEvents.slice(-20);
      });
    } catch (err) {
      console.error('应用仿真配置失败:', err);
      setError('应用仿真配置失败，请稍后重试');
    } finally {
      setLoading(false);
    }
  };
  
  // 格式化仿真时间
  const formatSimTime = (timeInSeconds) => {
    const hours = Math.floor(timeInSeconds / 3600);
    const minutes = Math.floor((timeInSeconds % 3600) / 60);
    const seconds = Math.floor(timeInSeconds % 60);
    
    return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
  };
  
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
          </Col>
          
          {/* 控制按钮卡片 */}
          <Col span={8}>
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
          </Col>
          
          {/* 事件日志卡片 */}
          <Col span={8}>
            <Card 
              title="事件日志" 
              className="events-card"
              extra={
                <Button 
                  type="text" 
                  icon={<ReloadOutlined />} 
                  onClick={() => setEvents([])}
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
          </Col>
        </Row>
        
        {/* 仿真参数卡片 */}
        <Card title="仿真参数配置" style={{ marginTop: 16 }}>
          <Row gutter={16}>
            <Col span={8}>
              <Card type="inner" title="环境参数">
                <Form layout="vertical">
                  <Form.Item label="地图大小">
                    <InputNumber
                      addonBefore="宽"
                      style={{ width: 120 }}
                      value={envParams.mapWidth}
                      onChange={(value) => handleEnvParamChange('mapWidth', value)}
                    /> x
                    <InputNumber
                      addonBefore="高"
                      style={{ width: 120 }}
                      value={envParams.mapHeight}
                      onChange={(value) => handleEnvParamChange('mapHeight', value)}
                    /> 米
                  </Form.Item>
                  
                  <Form.Item label="障碍物密度">
                    <Slider
                      value={envParams.obstaclesDensity}
                      onChange={(value) => handleEnvParamChange('obstaclesDensity', value)}
                    />
                  </Form.Item>
                  
                  <Form.Item label="天气条件">
                    <Select
                      value={envParams.weather}
                      onChange={(value) => handleEnvParamChange('weather', value)}
                    >
                      <Option value="sunny">晴天</Option>
                      <Option value="cloudy">多云</Option>
                      <Option value="rainy">雨天</Option>
                      <Option value="windy">大风</Option>
                    </Select>
                  </Form.Item>
                </Form>
              </Card>
            </Col>
            
            <Col span={8}>
              <Card type="inner" title="资源参数">
                <Form layout="vertical">
                  <Form.Item label="CPU资源">
                    <InputNumber
                      addonAfter="核心"
                      style={{ width: 150 }}
                      value={envParams.cpuResources}
                      onChange={(value) => handleEnvParamChange('cpuResources', value)}
                    />
                  </Form.Item>
                  
                  <Form.Item label="内存资源">
                    <InputNumber
                      addonAfter="GB"
                      style={{ width: 150 }}
                      value={envParams.memoryResources}
                      onChange={(value) => handleEnvParamChange('memoryResources', value)}
                    />
                  </Form.Item>
                  
                  <Form.Item label="带宽限制">
                    <InputNumber
                      addonAfter="Mbps"
                      style={{ width: 150 }}
                      value={envParams.bandwidthLimit}
                      onChange={(value) => handleEnvParamChange('bandwidthLimit', value)}
                    />
                  </Form.Item>
                </Form>
              </Card>
            </Col>
            
            <Col span={8}>
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
                    />
                  </Form.Item>
                </Form>
              </Card>
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
      </Spin>
    </div>
  );
};

export default SimulationControl;