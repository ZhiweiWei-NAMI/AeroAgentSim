import React, { createContext, useContext, useState, useEffect, useRef } from 'react';
import { simulationApi, connectWebSocket, sendWebSocketMessage, agentApi, workflowApi } from '../services/api';

// 创建上下文
const SimulationContext = createContext();

// 格式化仿真时间
export const formatSimTime = (timeInSeconds) => {
  const hours = Math.floor(timeInSeconds / 3600);
  const minutes = Math.floor((timeInSeconds % 3600) / 60);
  const seconds = Math.floor(timeInSeconds % 60);
  
  return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
};

// 上下文提供者组件
export const SimulationProvider = ({ children }) => {
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
  
  // 环境参数状态
  const [envParams, setEnvParams] = useState({
    mapWidth: 1000,
    mapHeight: 1000,
    obstaclesDensity: 30,
    weather: 'sunny',
    randomSeed: 42,
    logLevel: 'info',
    maxSimTime: 3600,
    // OSM地图参数
    mapCenterLat: 39.9042, // 地图中心纬度
    mapCenterLng: 116.4074, // 地图中心经度
    mapRadius: 1.0, // 地图半径（公里）
    loadOsmBuildings: true, // 是否加载OSM建筑物
    
    // 交通流仿真参数
    trafficSource: 'none', // none, csv, sumo
    trafficCsvFile: '',    // CSV文件路径
    sumoConfigFile: '',    // SUMO配置文件路径
    sumoPort: 8813,        // SUMO TraCI服务器端口
    sumoGui: false,         // 是否使用SUMO-GUI
    sumoNetworkGenerated: false // 是否已生成SUMO路网文件
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
        landing_spots: [],
        // 交通流仿真配置
        traffic: {
          source: envParams.trafficSource,
          csv_file: envParams.trafficCsvFile,
          sumo_config: envParams.trafficSource === 'sumo' ? {
            config_file: envParams.sumoConfigFile,
            port: envParams.sumoPort,
            gui: envParams.sumoGui,
            osm_center: [envParams.mapCenterLat, envParams.mapCenterLng],
            osm_radius: envParams.mapRadius
          } : null
        },
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
      
      // 保存地图中心坐标和交通配置到localStorage
      localStorage.setItem('mapCenterLat', envParams.mapCenterLat.toString());
      localStorage.setItem('mapCenterLng', envParams.mapCenterLng.toString());
      localStorage.setItem('mapRadius', envParams.mapRadius.toString());
      localStorage.setItem('loadOsmBuildings', envParams.loadOsmBuildings.toString());
      localStorage.setItem('trafficSource', envParams.trafficSource);
      if (envParams.trafficSource === 'csv') {
        localStorage.setItem('trafficCsvFile', envParams.trafficCsvFile);
      } else if (envParams.trafficSource === 'sumo') {
        localStorage.setItem('sumoConfigFile', envParams.sumoConfigFile);
        localStorage.setItem('sumoPort', envParams.sumoPort.toString());
        localStorage.setItem('sumoGui', envParams.sumoGui.toString());
      }
      
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

  // 清空事件
  const clearEvents = () => {
    setEvents([]);
  };

  // 上下文值
  const contextValue = {
    simulationStatus,
    simulationTime,
    simulationSpeed,
    events,
    loading,
    error,
    connected,
    agents,
    workflows,
    envParams,
    handleStartSimulation,
    handlePauseSimulation,
    handleResumeSimulation,
    handleResetSimulation,
    handleSpeedChange,
    handleEnvParamChange,
    handleApplySettings,
    clearEvents,
    webSocketRef
  };

  return (
    <SimulationContext.Provider value={contextValue}>
      {children}
    </SimulationContext.Provider>
  );
};

// 自定义Hook，用于在组件中访问上下文
export const useSimulation = () => {
  const context = useContext(SimulationContext);
  if (!context) {
    throw new Error('useSimulation必须在SimulationProvider内部使用');
  }
  return context;
};