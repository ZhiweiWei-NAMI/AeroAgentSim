# AirFogSim 前端项目设置指南

本文档提供了设置 AirFogSim 前端项目的详细步骤，包括后端 FastAPI 服务和前端 Vue.js 应用程序的创建和配置。

## 1. 项目结构

建议创建以下项目结构：

```
airfogsim-web/
├── backend/            # FastAPI 后端
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py     # FastAPI 入口点
│   │   ├── config.py   # 配置文件
│   │   ├── api/        # API 路由
│   │   ├── core/       # 核心功能
│   │   ├── models/     # 数据模型
│   │   └── services/   # 业务逻辑服务
│   ├── requirements.txt
│   └── README.md
└── frontend/           # Vue.js 前端
    ├── public/
    ├── src/
    │   ├── assets/
    │   ├── components/
    │   ├── views/
    │   ├── router/
    │   ├── store/
    │   ├── services/  # API 服务调用
    │   ├── App.vue
    │   └── main.js
    ├── package.json
    └── README.md
```

## 2. 后端设置 (FastAPI)

### 安装必要的依赖

创建一个 `requirements.txt` 文件，包含以下内容：

```
fastapi>=0.103.1
uvicorn>=0.23.2
websockets>=11.0.3
pydantic>=2.3.0
python-dotenv>=1.0.0
```

然后安装依赖：

```bash
# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # 在Windows上使用: venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 安装airfogsim (假设已经在开发模式下安装)
pip install -e ../path/to/airfogsim
```

### 创建 FastAPI 应用

在 `backend/app/main.py` 中创建基本的 FastAPI 应用：

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI(title="AirFogSim API", version="0.1.0")

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 在生产环境中，应该限制为前端域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"message": "Welcome to AirFogSim API"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
```

### 为 SimPy 集成创建适配器

在 `backend/app/core/simpy_adapter.py` 中创建适配器，用于连接 SimPy 仿真和 FastAPI：

```python
from typing import Dict, Any, Callable, List
import asyncio
import json
from fastapi import WebSocket
from airfogsim.core.environment import Environment

class SimulationAdapter:
    """SimPy仿真与FastAPI的适配器"""

    def __init__(self):
        self.env = None
        self.connected_clients: List[WebSocket] = []
        self.simulation_data = {
            "agents": {},
            "resources": {},
            "tasks": {},
            "workflows": {}
        }
        self.running = False
        
    async def connect_client(self, websocket: WebSocket):
        await websocket.accept()
        self.connected_clients.append(websocket)
        # 发送当前状态
        await websocket.send_text(json.dumps({
            "type": "initial_state",
            "data": self.simulation_data
        }))
    
    def disconnect_client(self, websocket: WebSocket):
        self.connected_clients.remove(websocket)
    
    async def broadcast(self, message_type: str, data: Dict[str, Any]):
        """向所有连接的客户端广播消息"""
        if not self.connected_clients:
            return
            
        message = json.dumps({
            "type": message_type,
            "data": data
        })
        
        for client in self.connected_clients:
            try:
                await client.send_text(message)
            except Exception:
                # 处理断开连接的客户端
                self.connected_clients.remove(client)
    
    def setup_simulation(self, config: Dict[str, Any] = None):
        """设置新的模拟环境"""
        # 创建新环境实例
        self.env = Environment(visual_interval=1)
        
        # 将事件注册到观察器以广播到WebSocket
        self._setup_event_observers()
        
        # 应用配置（如果提供）
        if config:
            self._apply_configuration(config)
        
        return self.env
    
    def _setup_event_observers(self):
        """设置事件观察器以捕获模拟事件并推送到WebSocket"""
        if not self.env:
            return
            
        # 监听代理状态变化
        self.env.event_registry.subscribe(
            "agent.state_changed", 
            self._on_agent_state_changed
        )
        
        # 监听任务状态变化
        self.env.event_registry.subscribe(
            "task_started", 
            self._on_task_state_changed
        )
        self.env.event_registry.subscribe(
            "task_completed", 
            self._on_task_state_changed
        )
        
        # 监听资源状态变化
        self.env.event_registry.subscribe(
            "resource.allocated", 
            self._on_resource_state_changed
        )
        self.env.event_registry.subscribe(
            "resource.released", 
            self._on_resource_state_changed
        )
    
    def _on_agent_state_changed(self, event_data):
        """处理代理状态变化事件"""
        agent_id = event_data.get("agent_id", "unknown")
        state = event_data.get("state", {})
        
        # 更新内部状态
        if agent_id not in self.simulation_data["agents"]:
            self.simulation_data["agents"][agent_id] = {}
        
        self.simulation_data["agents"][agent_id].update(state)
        
        # 广播更新
        asyncio.create_task(
            self.broadcast("agent_update", {
                "agent_id": agent_id,
                "state": state,
                "timestamp": self.env.now if self.env else 0
            })
        )
    
    def _on_task_state_changed(self, event_data):
        """处理任务状态变化事件"""
        # 类似地实现任务状态更新
        pass
    
    def _on_resource_state_changed(self, event_data):
        """处理资源状态变化事件"""
        # 类似地实现资源状态更新
        pass
    
    def _apply_configuration(self, config):
        """应用模拟配置"""
        # 根据配置创建代理、资源等
        pass
    
    async def run_simulation(self, until=100):
        """运行模拟直到指定时间"""
        if not self.env:
            raise ValueError("Simulation environment not set up")
        
        self.running = True
        
        # 在后台线程中运行SimPy仿真
        def run_env():
            try:
                self.env.run(until=until)
            finally:
                self.running = False
        
        # 在事件循环中启动后台线程
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, run_env)
        
        # 广播模拟完成消息
        await self.broadcast("simulation_completed", {
            "timestamp": self.env.now,
            "status": "completed"
        })
    
    def stop_simulation(self):
        """停止当前模拟"""
        if self.env and self.running:
            # 这里需要实现一种方式来停止SimPy环境
            # 可能需要修改AirFogSim的代码以支持这一功能
            self.running = False

# 创建单例实例
simulation_adapter = SimulationAdapter()
```

### 添加 WebSocket 端点

在 `backend/app/api/websocket.py` 中添加 WebSocket 端点：

```python
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from ..core.simpy_adapter import simulation_adapter

router = APIRouter()

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await simulation_adapter.connect_client(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # 处理从客户端收到的消息（如果需要）
    except WebSocketDisconnect:
        simulation_adapter.disconnect_client(websocket)
```

### 添加 REST API 端点

在 `backend/app/api/simulation.py` 中添加 API 端点：

```python
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Dict, Any, Optional
from ..core.simpy_adapter import simulation_adapter

router = APIRouter()

class SimulationConfig(BaseModel):
    agents: Optional[Dict[str, Any]] = None
    resources: Optional[Dict[str, Any]] = None
    workflows: Optional[Dict[str, Any]] = None
    duration: Optional[int] = 100

@router.post("/configure")
async def configure_simulation(config: SimulationConfig):
    """配置新的模拟环境"""
    try:
        env = simulation_adapter.setup_simulation(config.dict())
        return {"status": "success", "message": "Simulation configured"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/start")
async def start_simulation(background_tasks: BackgroundTasks, duration: int = 100):
    """启动模拟"""
    try:
        # 在后台任务中运行模拟
        background_tasks.add_task(simulation_adapter.run_simulation, duration)
        return {"status": "success", "message": "Simulation started"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/stop")
async def stop_simulation():
    """停止模拟"""
    try:
        simulation_adapter.stop_simulation()
        return {"status": "success", "message": "Simulation stopped"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/status")
async def get_simulation_status():
    """获取模拟状态"""
    return {
        "running": simulation_adapter.running,
        "time": simulation_adapter.env.now if simulation_adapter.env else 0,
        "agents_count": len(simulation_adapter.simulation_data["agents"]),
        "resources_count": len(simulation_adapter.simulation_data["resources"])
    }
```

### 更新主应用

更新 `backend/app/main.py` 添加路由：

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from app.api import simulation, websocket

app = FastAPI(title="AirFogSim API", version="0.1.0")

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 添加路由
app.include_router(simulation.router, prefix="/api/simulation", tags=["simulation"])
app.include_router(websocket.router, tags=["websocket"])

@app.get("/")
async def root():
    return {"message": "Welcome to AirFogSim API"}

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
```

## 3. 前端设置 (Vue.js)

### 创建 Vue 项目

使用 Vue CLI 创建一个新项目：

```bash
npm install -g @vue/cli
vue create frontend
```

在交互式提示中，选择：
- Vue 3
- Babel
- Router
- Vuex
- CSS Pre-processors (选择 SCSS)
- Linter / Formatter (ESLint + Prettier)

### 安装必要的依赖

在前端项目目录中安装额外的依赖：

```bash
cd frontend
npm install axios leaflet echarts element-plus
```

### 配置环境变量

创建 `.env.development` 文件：

```
VUE_APP_API_URL=http://localhost:8000
VUE_APP_WS_URL=ws://localhost:8000/ws
```

### 创建 API 服务

在 `frontend/src/services/api.js` 中创建 API 服务：

```javascript
import axios from 'axios';

const apiClient = axios.create({
  baseURL: process.env.VUE_APP_API_URL,
  headers: {
    'Content-Type': 'application/json'
  }
});

export default {
  // 模拟控制
  configureSimulation(config) {
    return apiClient.post('/api/simulation/configure', config);
  },
  startSimulation(duration = 100) {
    return apiClient.post('/api/simulation/start', { duration });
  },
  stopSimulation() {
    return apiClient.post('/api/simulation/stop');
  },
  getSimulationStatus() {
    return apiClient.get('/api/simulation/status');
  },
  
  // 代理相关API
  getAgents() {
    return apiClient.get('/api/agents');
  },
  createAgent(agentData) {
    return apiClient.post('/api/agents', agentData);
  },
  
  // 资源相关API
  getResources() {
    return apiClient.get('/api/resources');
  },
  createResource(type, resourceData) {
    return apiClient.post(`/api/resources/${type}`, resourceData);
  }
};
```

### 创建 WebSocket 服务

在 `frontend/src/services/websocket.js` 中创建 WebSocket 服务：

```javascript
import { reactive } from 'vue';

// 创建一个响应式状态对象
export const simulationState = reactive({
  agents: {},
  resources: {},
  tasks: {},
  workflows: {},
  status: 'idle',
  currentTime: 0
});

let socket = null;
let reconnectTimer = null;
const listeners = [];

export const websocketService = {
  connect() {
    // 关闭已存在的连接
    if (socket) {
      this.disconnect();
    }
    
    // 创建新连接
    socket = new WebSocket(process.env.VUE_APP_WS_URL);
    
    socket.onopen = () => {
      console.log('WebSocket连接已建立');
      setError(null);
      simulationState.status = 'connected';
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
    };
    
    socket.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        this.handleMessage(message);
      } catch (error) {
        console.error('解析WebSocket消息时出错:', error);
      }
    };
    
    socket.onclose = () => {
      console.log('WebSocket连接已关闭');
      simulationState.status = 'disconnected';
      
      // 尝试重新连接
      if (!reconnectTimer) {
        reconnectTimer = setTimeout(() => this.connect(), 5000);
      }
    };
    
    socket.onerror = (error) => {
      console.error('WebSocket错误:', error);
      simulationState.status = 'error';
    };
  },
  
  disconnect() {
    if (socket) {
      socket.close();
      socket = null;
    }
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  },
  
  handleMessage({ type, data }) {
    switch (type) {
      case 'initial_state':
        // 初始化状态
        Object.assign(simulationState, data);
        break;
        
      case 'agent_update':
        // 更新代理状态
        if (!simulationState.agents[data.agent_id]) {
          simulationState.agents[data.agent_id] = {};
        }
        Object.assign(simulationState.agents[data.agent_id], data.state);
        simulationState.currentTime = data.timestamp;
        break;
        
      case 'resource_update':
        // 更新资源状态
        if (!simulationState.resources[data.resource_id]) {
          simulationState.resources[data.resource_id] = {};
        }
        Object.assign(simulationState.resources[data.resource_id], data.state);
        break;
        
      case 'task_update':
        // 更新任务状态
        simulationState.tasks[data.task_id] = data.state;
        break;
        
      case 'workflow_update':
        // 更新工作流状态
        simulationState.workflows[data.workflow_id] = data.state;
        break;
        
      case 'simulation_completed':
        // 模拟完成
        simulationState.status = 'completed';
        simulationState.currentTime = data.timestamp;
        break;
        
      default:
        // 通知其他监听器
        listeners.forEach(listener => {
          if (listener.type === type) {
            listener.callback(data);
          }
        });
    }
  },
  
  addListener(type, callback) {
    const listener = { type, callback };
    listeners.push(listener);
    return () => {
      const index = listeners.indexOf(listener);
      if (index !== -1) {
        listeners.splice(index, 1);
      }
    };
  }
};
```

### 创建主要组件

#### 创建地图组件

在 `frontend/src/components/MapView.vue` 中创建地图组件：

```vue
<template>
  <div class="map-container">
    <div id="map" ref="mapRef"></div>
  </div>
</template>

<script>
import { ref, onMounted, onUnmounted, watch } from 'vue';
import 'leaflet/dist/leaflet.css';
import L from 'leaflet';
import { simulationState } from '@/services/websocket';

export default {
  name: 'MapView',
  setup() {
    const mapRef = ref(null);
    let map = null;
    let agentMarkers = {};
    let landingSpotMarkers = {};
    let airspacePolygons = {};
    
    // 初始化地图
    const initMap = () => {
      if (!mapRef.value) return;
      
      // 创建地图
      map = L.map(mapRef.value).setView([0, 0], 13);
      
      // 添加底图
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap contributors'
      }).addTo(map);
      
      // 调整地图视图以包含所有实体
      updateMapBounds();
    };
    
    // 更新地图边界以包含所有实体
    const updateMapBounds = () => {
      if (!map) return;
      
      const bounds = L.latLngBounds();
      let hasBounds = false;
      
      // 添加所有代理位置
      Object.values(simulationState.agents).forEach(agent => {
        if (agent.position) {
          const [x, y, z] = agent.position;
          bounds.extend([y, x]);  // 注意：这里x,y映射到lng,lat
          hasBounds = true;
        }
      });
      
      // 添加所有着陆点位置
      Object.values(simulationState.resources).forEach(resource => {
        if (resource.type === 'landing_spot' && resource.location) {
          const [x, y, z] = resource.location;
          bounds.extend([y, x]);
          hasBounds = true;
        }
      });
      
      if (hasBounds) {
        map.fitBounds(bounds, { padding: [50, 50] });
      } else {
        // 如果没有实体，设置默认视图
        map.setView([0, 0], 13);
      }
    };
    
    // 更新代理标记
    const updateAgentMarkers = () => {
      if (!map) return;
      
      // 更新现有代理和添加新代理
      Object.entries(simulationState.agents).forEach(([agentId, agent]) => {
        if (agent.position) {
          const [x, y, z] = agent.position;
          
          if (agentMarkers[agentId]) {
            // 更新现有标记
            agentMarkers[agentId].setLatLng([y, x]);
            
            // 更新弹出窗内容
            const popupContent = `
              <b>${agentId}</b><br>
              位置: (${x.toFixed(2)}, ${y.toFixed(2)}, ${z.toFixed(2)})<br>
              电量: ${agent.battery ? `${agent.battery.toFixed(2)}%` : 'N/A'}<br>
              状态: ${agent.status || 'N/A'}
            `;
            agentMarkers[agentId].setPopupContent(popupContent);
          } else {
            // 创建新标记
            const marker = L.marker([y, x], {
              icon: L.divIcon({
                className: 'agent-marker',
                html: `<div class="agent-icon drone-icon">${agentId}</div>`,
                iconSize: [30, 30]
              })
            });
            
            // 添加弹出窗
            const popupContent = `
              <b>${agentId}</b><br>
              位置: (${x.toFixed(2)}, ${y.toFixed(2)}, ${z.toFixed(2)})<br>
              电量: ${agent.battery ? `${agent.battery.toFixed(2)}%` : 'N/A'}<br>
              状态: ${agent.status || 'N/A'}
            `;
            marker.bindPopup(popupContent);
            
            marker.addTo(map);
            agentMarkers[agentId] = marker;
          }
        }
      });
      
      // 移除不再存在的代理标记
      Object.keys(agentMarkers).forEach(agentId => {
        if (!simulationState.agents[agentId] || !simulationState.agents[agentId].position) {
          map.removeLayer(agentMarkers[agentId]);
          delete agentMarkers[agentId];
        }
      });
    };
    
    // 监视模拟状态变化
    watch(() => simulationState.agents, updateAgentMarkers, { deep: true });
    
    // 监视资源状态变化
    watch(() => simulationState.resources, () => {
      // 更新着陆点和空域
      // 类似于updateAgentMarkers实现
    }, { deep: true });
    
    onMounted(() => {
      initMap();
      updateAgentMarkers();
    });
    
    onUnmounted(() => {
      if (map) {
        map.remove();
        map = null;
      }
    });
    
    return {
      mapRef
    };
  }
};
</script>

<style scoped>
.map-container {
  width: 100%;
  height: 100%;
}

#map {
  width: 100%;
  height: 100%;
}

:deep(.agent-marker) {
  display: flex;
  align-items: center;
  justify-content: center;
}

:deep(.drone-icon) {
  background-color: #4CAF50;
  color: white;
  border-radius: 50%;
  width: 30px;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: bold;
}
</style>
```

#### 创建控制面板组件

在 `frontend/src/components/ControlPanel.vue` 中创建控制面板组件：

```vue
<template>
  <div class="control-panel">
    <el-card class="box-card">
      <template #header>
        <div class="card-header">
          <span>仿真控制</span>
        </div>
      </template>
      
      <div class="control-actions">
        <el-button type="primary" @click="startSimulation" :disabled="isRunning">
          开始
        </el-button>
        <el-button type="warning" @click="stopSimulation" :disabled="!isRunning">
          停止
        </el-button>
        <el-button type="info" @click="configureSimulation">
          配置
        </el-button>
      </div>
      
      <div class="simulation-status">
        <p><strong>状态:</strong> {{ statusText }}</p>
        <p><strong>当前时间:</strong> {{ simulationState.currentTime.toFixed(2) }}</p>
      </div>
      
      <div class="agents-list">
        <h4>无人机列表</h4>
        <el-table :data="agentsList" size="small" style="width: 100%">
          <el-table-column prop="id" label="ID" width="80" />
          <el-table-column prop="battery" label="电量" width="80">
            <template #default="scope">
              {{ scope.row.battery ? `${scope.row.battery.toFixed(0)}%` : 'N/A' }}
            </template>
          </el-table-column>
          <el-table-column prop="status" label="状态" />
        </el-table>
      </div>
    </el-card>
  </div>
</template>

<script>
import { computed, ref } from 'vue';
import { simulationState } from '@/services/websocket';
import api from '@/services/api';
import { ElMessage } from 'element-plus';

export default {
  name: 'ControlPanel',
  setup() {
    const isRunning = ref(false);
    
    // 检查模拟状态
    const checkStatus = async () => {
      try {
        const response = await api.getSimulationStatus();
        isRunning.value = response.data.running;
      } catch (error) {
        console.error('获取模拟状态失败:', error);
        ElMessage.error('获取模拟状态失败');
      }
    };
    
    // 启动模拟
    const startSimulation = async () => {
      try {
        await api.startSimulation(1000);
        isRunning.value = true;
        ElMessage.success('模拟已启动');
      } catch (error) {
        console.error('启动模拟失败:', error);
        ElMessage.error('启动模拟失败');
      }
    };
    
    // 停止模拟
    const stopSimulation = async () => {
      try {
        await api.stopSimulation();
        isRunning.value = false;
        ElMessage.success('模拟已停止');
      } catch (error) {
        console.error('停止模拟失败:', error);
        ElMessage.error('停止模拟失败');
      }
    };
    
    // 配置模拟
    const configureSimulation = () => {
      // 这里可以打开配置对话框
      ElMessage.info('配置功能将在后续版本中实现');
    };
    
    // 计算属性：代理列表
    const agentsList = computed(() => {
      return Object.entries(simulationState.agents).map(([id, agent]) => ({
        id,
        battery: agent.battery,
        status: agent.status || '未知'
      }));
    });
    
    // 计算状态文本
    const statusText = computed(() => {
      if (isRunning.value) {
        return '运行中';
      } else if (simulationState.status === 'completed') {
        return '已完成';
      } else {
        return '就绪';
      }
    });
    
    // 初始检查状态
    checkStatus();
    
    return {
      simulationState,
      isRunning,
      startSimulation,
      stopSimulation,
      configureSimulation,
      agentsList,
      statusText
    };
  }
};
</script>

<style scoped>
.control-panel {
  padding: 10px;
  width: 100%;
  height: 100%;
  overflow-y: auto;
}

.control-actions {
  display: flex;
  justify-content: space-between;
  margin-bottom: 20px;
}

.simulation-status {
  margin-bottom: 20px;
}

.agents-list {
  margin-top: 20px;
}
</style>
```

#### 创建数据分析面板

在 `frontend/src/components/DataPanel.vue` 中创建数据分析面板：

```vue
<template>
  <div class="data-panel">
    <el-tabs v-model="activeTab">
      <el-tab-pane label="资源使用" name="resources">
        <div class="chart-container" ref="resourceChartRef"></div>
      </el-tab-pane>
      <el-tab-pane label="代理状态" name="agents">
        <div class="chart-container" ref="agentChartRef"></div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script>
import { ref, onMounted, onUnmounted, watch } from 'vue';
import * as echarts from 'echarts';
import { simulationState } from '@/services/websocket';

export default {
  name: 'DataPanel',
  setup() {
    const activeTab = ref('resources');
    const resourceChartRef = ref(null);
    const agentChartRef = ref(null);
    let resourceChart = null;
    let agentChart = null;
    
    // 初始化资源使用图表
    const initResourceChart = () => {
      if (!resourceChartRef.value) return;
      
      resourceChart = echarts.init(resourceChartRef.value);
      
      const option = {
        title: {
          text: '资源使用情况'
        },
        tooltip: {
          trigger: 'axis',
          axisPointer: {
            type: 'shadow'
          }
        },
        legend: {
          data: ['已使用', '总容量']
        },
        grid: {
          left: '3%',
          right: '4%',
          bottom: '3%',
          containLabel: true
        },
        xAxis: {
          type: 'category',
          data: []
        },
        yAxis: {
          type: 'value'
        },
        series: [
          {
            name: '已使用',
            type: 'bar',
            data: []
          },
          {
            name: '总容量',
            type: 'bar',
            data: []
          }
        ]
      };
      
      resourceChart.setOption(option);
      
      // 监听窗口大小变化
      window.addEventListener('resize', () => {
        resourceChart?.resize();
      });
    };
    
    // 初始化代理状态图表
    const initAgentChart = () => {
      if (!agentChartRef.value) return;
      
      agentChart = echarts.init(agentChartRef.value);
      
      const option = {
        title: {
          text: '无人机电量状态'
        },
        tooltip: {
          trigger: 'axis'
        },
        legend: {
          data: ['电量百分比']
        },
        grid: {
          left: '3%',
          right: '4%',
          bottom: '3%',
          containLabel: true
        },
        xAxis: {
          type: 'category',
          data: []
        },
        yAxis: {
          type: 'value',
          max: 100,
          min: 0
        },
        series: [
          {
            name: '电量百分比',
            type: 'bar',
            data: []
          }
        ]
      };
      
      agentChart.setOption(option);
      
      // 监听窗口大小变化
      window.addEventListener('resize', () => {
        agentChart?.resize();
      });
    };
    
    // 更新资源图表数据
    const updateResourceChart = () => {
      if (!resourceChart) return;
      
      const resources = simulationState.resources;
      const resourceTypes = {};
      
      // 按类型分组资源
      Object.values(resources).forEach(resource => {
        if (!resource.type) return;
        
        if (!resourceTypes[resource.type]) {
          resourceTypes[resource.type] = {
            total: 0,
            used: 0
          };
        }
        
        resourceTypes[resource.type].total += resource.max_capacity || 0;
        resourceTypes[resource.type].used += resource.current_usage || 0;
      });
      
      const types = Object.keys(resourceTypes);
      const usedData = types.map(type => resourceTypes[type].used);
      const totalData = types.map(type => resourceTypes[type].total);
      
      resourceChart.setOption({
        xAxis: {
          data: types
        },
        series: [
          {
            name: '已使用',
            data: usedData
          },
          {
            name: '总容量',
            data: totalData
          }
        ]
      });
    };
    
    // 更新代理图表数据
    const updateAgentChart = () => {
      if (!agentChart) return;
      
      const agents = simulationState.agents;
      const agentIds = Object.keys(agents);
      const batteryData = agentIds.map(id => agents[id].battery || 0);
      
      agentChart.setOption({
        xAxis: {
          data: agentIds
        },
        series: [
          {
            name: '电量百分比',
            data: batteryData
          }
        ]
      });
    };
    
    // 监视模拟状态变化
    watch(() => simulationState.resources, updateResourceChart, { deep: true });
    watch(() => simulationState.agents, updateAgentChart, { deep: true });
    
    onMounted(() => {
      initResourceChart();
      initAgentChart();
      updateResourceChart();
      updateAgentChart();
    });
    
    onUnmounted(() => {
      resourceChart?.dispose();
      agentChart?.dispose();
      resourceChart = null;
      agentChart = null;
    });
    
    return {
      activeTab,
      resourceChartRef,
      agentChartRef
    };
  }
};
</script>

<style scoped>
.data-panel {
  width: 100%;
  height: 100%;
  padding: 10px;
}

.chart-container {
  width: 100%;
  height: 300px;
}
</style>
```

### 创建主要视图

在 `frontend/src/views/Dashboard.vue` 中创建仪表板视图：

```vue
<template>
  <div class="dashboard-container">
    <el-container>
      <el-aside width="300px">
        <control-panel />
