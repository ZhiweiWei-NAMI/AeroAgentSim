import axios from 'axios';

// 配置axios默认设置
const API_BASE_URL = process.env.REACT_APP_API_BASE_URL || 'http://localhost:8000/api';
const WS_BASE_URL = process.env.REACT_APP_WS_BASE_URL || 'ws://localhost:8000/ws';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// 无人机API
const droneApi = {
  // 获取所有无人机列表
  getAllDrones: async () => {
    try {
      const response = await apiClient.get('/drones');
      return response.data;
    } catch (error) {
      console.error('获取无人机列表失败:', error);
      throw error;
    }
  },
  
  // 获取特定无人机信息
  getDrone: async (droneId) => {
    try {
      const response = await apiClient.get(`/drones/${droneId}`);
      return response.data;
    } catch (error) {
      console.error(`获取无人机(ID: ${droneId})信息失败:`, error);
      throw error;
    }
  },
  
  // 获取无人机历史数据
  getDroneHistory: async (droneId, startTime, endTime, limit = 100) => {
    try {
      const params = { limit };
      if (startTime) params.start_time = startTime;
      if (endTime) params.end_time = endTime;
      
      const response = await apiClient.get(`/drones/${droneId}/history`, { params });
      return response.data;
    } catch (error) {
      console.error(`获取无人机(ID: ${droneId})历史数据失败:`, error);
      throw error;
    }
  },
  
  // 获取无人机轨迹数据
  getDroneTrajectory: async (droneId, startTime, endTime, interval = 1.0) => {
    try {
      const params = { interval };
      if (startTime) params.start_time = startTime;
      if (endTime) params.end_time = endTime;
      
      const response = await apiClient.get(`/drones/${droneId}/trajectory`, { params });
      return response.data;
    } catch (error) {
      console.error(`获取无人机(ID: ${droneId})轨迹数据失败:`, error);
      throw error;
    }
  }
};

// 工作流API
const workflowApi = {
  // 获取所有工作流
  getAllWorkflows: async () => {
    try {
      const response = await apiClient.get('/workflows');
      return response.data;
    } catch (error) {
      console.error('获取工作流列表失败:', error);
      throw error;
    }
  },
  
  // 获取特定工作流
  getWorkflow: async (workflowId) => {
    try {
      const response = await apiClient.get(`/workflows/${workflowId}`);
      return response.data;
    } catch (error) {
      console.error(`获取工作流(ID: ${workflowId})信息失败:`, error);
      throw error;
    }
  },
  
  // 创建新工作流
  createWorkflow: async (workflowData) => {
    try {
      const response = await apiClient.post('/workflows', workflowData);
      return response.data;
    } catch (error) {
      console.error('创建工作流失败:', error);
      throw error;
    }
  },
  
  // 删除工作流
  deleteWorkflow: async (workflowId) => {
    try {
      const response = await apiClient.delete(`/workflows/${workflowId}`);
      return response.data;
    } catch (error) {
      console.error(`删除工作流(ID: ${workflowId})失败:`, error);
      throw error;
    }
  },
  
  // 获取工作流模板
  getWorkflowTemplates: async () => {
    try {
      const response = await apiClient.get('/templates/workflows');
      return response.data;
    } catch (error) {
      console.error('获取工作流模板失败:', error);
      throw error;
    }
  }
};

// 智能体API
const agentApi = {
  // 获取所有智能体
  getAllAgents: async () => {
    try {
      const response = await apiClient.get('/agents');
      return response.data;
    } catch (error) {
      console.error('获取智能体列表失败:', error);
      throw error;
    }
  },
  
  // 获取特定智能体
  getAgent: async (agentId) => {
    try {
      const response = await apiClient.get(`/agents/${agentId}`);
      return response.data;
    } catch (error) {
      console.error(`获取智能体(ID: ${agentId})信息失败:`, error);
      throw error;
    }
  },
  
  // 创建新智能体
  createAgent: async (agentData) => {
    try {
      const response = await apiClient.post('/agents', agentData);
      return response.data;
    } catch (error) {
      console.error('创建智能体失败:', error);
      throw error;
    }
  },
  
  // 删除智能体
  deleteAgent: async (agentId) => {
    try {
      const response = await apiClient.delete(`/agents/${agentId}`);
      return response.data;
    } catch (error) {
      console.error(`删除智能体(ID: ${agentId})失败:`, error);
      throw error;
    }
  },
  
  // 获取智能体模板
  getAgentTemplates: async () => {
    try {
      const response = await apiClient.get('/templates/agents');
      return response.data;
    } catch (error) {
      console.error('获取智能体模板失败:', error);
      throw error;
    }
  }
};

// 车辆API
const vehicleApi = {
  // 获取所有车辆位置信息
  getAllVehicles: async () => {
    try {
      // 尝试从后端获取数据
      const response = await apiClient.get('/vehicles');
      // 对获取的数据进行处理，交换 position 的 y 和 z 坐标
      const processedData = response.data.map(vehicle => {
        if (vehicle.position && vehicle.position.length === 3) {
          const [x, y, z] = vehicle.position;
          // vehicle.angle是度数,要转为PI
          vehicle.angle = (vehicle.angle / 180) * Math.PI;
          return { ...vehicle, position: [x, z, y] }; // 交换 y 和 z
        }
        return vehicle; // 如果 position 不符合预期，则返回原始数据
      });
      return processedData;
    } catch (error) {
      console.warn('获取车辆列表失败:', error.message);
      // 不再返回模拟数据，而是返回空数组
      return [];
    }
  },
  
  // 获取特定车辆信息
  getVehicle: async (vehicleId) => {
    try {
      const response = await apiClient.get(`/vehicles/${vehicleId}`);
      return response.data;
    } catch (error) {
      console.error(`获取车辆(ID: ${vehicleId})信息失败:`, error);
      throw error;
    }
  }
};

// 仿真控制API
const simulationApi = {
  // 启动仿真
  startSimulation: async () => {
    try {
      const response = await apiClient.post('/simulation/start');
      return response.data;
    } catch (error) {
      console.error('启动仿真失败:', error);
      throw error;
    }
  },
  
  // 暂停仿真
  pauseSimulation: async () => {
    try {
      const response = await apiClient.post('/simulation/pause');
      return response.data;
    } catch (error) {
      console.error('暂停仿真失败:', error);
      throw error;
    }
  },
  
  // 恢复仿真
  resumeSimulation: async () => {
    try {
      const response = await apiClient.post('/simulation/resume');
      return response.data;
    } catch (error) {
      console.error('恢复仿真失败:', error);
      throw error;
    }
  },
  
  // 重置仿真
  resetSimulation: async () => {
    try {
      const response = await apiClient.post('/simulation/reset');
      return response.data;
    } catch (error) {
      console.error('重置仿真失败:', error);
      throw error;
    }
  },
  
  // 配置仿真环境
  configureSimulation: async (config) => {
    try {
      const response = await apiClient.post('/simulation/configure', config);
      return response.data;
    } catch (error) {
      console.error('配置仿真环境失败:', error);
      throw error;
    }
  }
};

// WebSocket连接
const connectWebSocket = (onMessage, onOpen, onClose, onError) => {
  console.log('尝试连接WebSocket:', WS_BASE_URL);
  const socket = new WebSocket(WS_BASE_URL);
  
  socket.onopen = () => {
    console.log('WebSocket连接已建立');
    if (onOpen) onOpen();
  };
  
  // 在connectWebSocket函数中添加心跳处理
  socket.onmessage = (event) => {
    try {
        const data = JSON.parse(event.data);
        
        // 处理心跳消息
        if (data.type === "heartbeat") {
            console.log("收到心跳消息");
            // 发送心跳响应
            if (socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({
                    type: "heartbeat_response",
                    timestamp: Date.now()
                }));
            }
            return;
        }
        
        if (onMessage) onMessage(data);
    } catch (error) {
        console.error('解析WebSocket消息失败:', error);
    }
  };
  
  socket.onclose = (event) => {
    console.log('WebSocket连接已关闭:', event.code, event.reason, '是否清洁关闭:', event.wasClean);
    if (onClose) onClose(event);
  };
  
  socket.onerror = (error) => {
    console.error('WebSocket错误:', error);
    if (onError) onError(error);
  };
  
  return socket;
};

// 发送WebSocket消息
const sendWebSocketMessage = (socket, type, command, data = {}) => {
  if (socket && socket.readyState === WebSocket.OPEN) {
    const message = JSON.stringify({
      type,
      command,
      ...data
    });
    socket.send(message);
    return true;
  }
  return false;
};

// 地图配置状态管理
let mapConfig = {
  center: [39.9042, 116.4074], // 默认北京中心
  radius: 1.0, // 默认1公里半径
  loadBuildings: false // 默认不加载建筑物
};

// 地图API
const mapApi = {
  // 获取当前地图配置
  getMapConfig: () => {
    return { ...mapConfig };
  },
  
  // 更新地图配置
  updateMapConfig: (config) => {
    mapConfig = { ...mapConfig, ...config };
    return mapConfig;
  }
};

// 导出所有API
export {
  droneApi,
  workflowApi,
  agentApi,
  simulationApi,
  mapApi,
  vehicleApi,
  connectWebSocket,
  sendWebSocketMessage
};