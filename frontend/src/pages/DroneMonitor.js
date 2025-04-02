import React, { useState, useEffect, useRef } from 'react';
import { Row, Col, Card, Select, Button, Statistic, Descriptions, Badge, Spin, Alert, Space } from 'antd';
import { 
  RocketOutlined, 
  ThunderboltOutlined, 
  EnvironmentOutlined,
  ReloadOutlined,
  LineChartOutlined
} from '@ant-design/icons';
import { droneApi, connectWebSocket } from '../services/api';

// 引入地图组件，这里假设使用react-leaflet
import { MapContainer, TileLayer, Marker, Popup, Polyline } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import L from 'leaflet';

// 修复Leaflet默认图标问题
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon-2x.png',
  iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
});

const { Option } = Select;

const DroneMonitor = () => {
  // 状态管理
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [drones, setDrones] = useState([]);
  const [selectedDroneId, setSelectedDroneId] = useState(null);
  const [selectedDrone, setSelectedDrone] = useState(null);
  const [trajectory, setTrajectory] = useState([]);
  const [webSocket, setWebSocket] = useState(null);
  const [mapCenter, setMapCenter] = useState([39.9042, 116.4074]); // 默认北京中心
  const [mapZoom, setMapZoom] = useState(12);
  const [showTrajectory, setShowTrajectory] = useState(true);
  const mapRef = useRef(null);

  // 初始化WebSocket连接
  useEffect(() => {
    const ws = connectWebSocket(
      // 消息处理函数
      (data) => {
        if (data.type === 'drone_update' && data.drone) {
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

          // 如果是当前选中的无人机，更新详情
          if (selectedDroneId && data.drone.id === selectedDroneId) {
            setSelectedDrone(data.drone);
            
            // 更新轨迹
            if (data.drone.position) {
              const position = Array.isArray(data.drone.position) 
                ? data.drone.position 
                : JSON.parse(data.drone.position);
                
              setTrajectory(prev => {
                // 添加新位置到轨迹
                const newTrajectory = [...prev, [position[1], position[0]]]; // 注意经纬度顺序
                // 最多保留100个点
                return newTrajectory.slice(-100);
              });
            }
          }
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
        setError('WebSocket连接出错，实时数据可能无法更新');
      }
    );

    setWebSocket(ws);

    // 组件卸载时关闭WebSocket连接
    return () => {
      if (ws) {
        ws.close();
      }
    };
  }, [selectedDroneId]);

  // 加载无人机列表
  useEffect(() => {
    const fetchDrones = async () => {
      setLoading(true);
      setError(null);
      try {
        const dronesData = await droneApi.getAllDrones();
        setDrones(dronesData);
        
        // 如果有无人机，默认选择第一个
        if (dronesData.length > 0 && !selectedDroneId) {
          setSelectedDroneId(dronesData[0].id);
        }
      } catch (err) {
        console.error('获取无人机列表失败:', err);
        setError('获取无人机列表失败，请检查网络连接或刷新页面重试');
      } finally {
        setLoading(false);
      }
    };

    fetchDrones();
  }, []);

  // 当选择的无人机ID变化时，获取无人机详情和轨迹
  useEffect(() => {
    if (!selectedDroneId) return;

    const fetchDroneDetails = async () => {
      setLoading(true);
      setError(null);
      try {
        // 获取无人机详情
        const droneData = await droneApi.getDrone(selectedDroneId);
        setSelectedDrone(droneData);
        
        // 获取无人机轨迹
        const trajectoryData = await droneApi.getDroneTrajectory(selectedDroneId);
        
        // 转换轨迹数据格式为地图可用的格式
        const trajectoryPoints = trajectoryData.map(point => {
          const position = Array.isArray(point.position) 
            ? point.position 
            : JSON.parse(point.position);
          return [position[1], position[0]]; // 注意经纬度顺序
        });
        
        setTrajectory(trajectoryPoints);
        
        // 如果有位置数据，更新地图中心
        if (droneData.position) {
          const position = Array.isArray(droneData.position) 
            ? droneData.position 
            : JSON.parse(droneData.position);
          setMapCenter([position[1], position[0]]); // 注意经纬度顺序
          
          // 如果地图已加载，设置视图
          if (mapRef.current) {
            mapRef.current.setView([position[1], position[0]], mapZoom);
          }
        }
      } catch (err) {
        console.error('获取无人机详情失败:', err);
        setError('获取无人机详情失败，请检查网络连接或刷新页面重试');
      } finally {
        setLoading(false);
      }
    };

    fetchDroneDetails();
  }, [selectedDroneId]);

  // 刷新无人机数据
  const handleRefresh = async () => {
    if (!selectedDroneId) return;
    
    setLoading(true);
    setError(null);
    try {
      // 获取无人机详情
      const droneData = await droneApi.getDrone(selectedDroneId);
      setSelectedDrone(droneData);
      
      // 获取无人机轨迹
      const trajectoryData = await droneApi.getDroneTrajectory(selectedDroneId);
      
      // 转换轨迹数据格式
      const trajectoryPoints = trajectoryData.map(point => {
        const position = Array.isArray(point.position) 
          ? point.position 
          : JSON.parse(point.position);
        return [position[1], position[0]]; // 注意经纬度顺序
      });
      
      setTrajectory(trajectoryPoints);
    } catch (err) {
      console.error('刷新无人机数据失败:', err);
      setError('刷新无人机数据失败，请检查网络连接或重试');
    } finally {
      setLoading(false);
    }
  };

  // 切换轨迹显示
  const toggleTrajectory = () => {
    setShowTrajectory(!showTrajectory);
  };

  // 获取无人机状态对应的颜色
  const getDroneStatusColor = (status) => {
    switch (status) {
      case 'idle':
        return 'blue';
      case 'moving':
        return 'green';
      case 'charging':
        return 'orange';
      case 'error':
        return 'red';
      default:
        return 'default';
    }
  };

  // 获取电池电量对应的颜色
  const getBatteryColor = (level) => {
    if (level < 20) return 'red';
    if (level < 50) return 'orange';
    return 'green';
  };

  if (loading && !selectedDrone) {
    return (
      <div style={{ textAlign: 'center', padding: '50px' }}>
        <Spin size="large" tip="加载中..." />
      </div>
    );
  }

  return (
    <div className="drone-monitor">
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

      {/* 无人机选择和控制 */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={12}>
          <Space>
            <Select
              placeholder="选择无人机"
              style={{ width: 200 }}
              value={selectedDroneId}
              onChange={setSelectedDroneId}
              loading={loading}
            >
              {drones.map(drone => (
                <Option key={drone.id} value={drone.id}>
                  {drone.id}
                </Option>
              ))}
            </Select>
            <Button 
              icon={<ReloadOutlined />} 
              onClick={handleRefresh}
              loading={loading}
            >
              刷新
            </Button>
            <Button 
              icon={<LineChartOutlined />} 
              onClick={toggleTrajectory}
              type={showTrajectory ? 'primary' : 'default'}
            >
              {showTrajectory ? '隐藏轨迹' : '显示轨迹'}
            </Button>
          </Space>
        </Col>
        <Col span={12} style={{ textAlign: 'right' }}>
          <Space>
            <Button type="primary" href={`/drones/${selectedDroneId}/history`}>
              查看历史数据
            </Button>
          </Space>
        </Col>
      </Row>

      {/* 无人机状态和地图 */}
      <Row gutter={16}>
        {/* 无人机状态卡片 */}
        <Col span={8}>
          {selectedDrone ? (
            <>
              <Card style={{ marginBottom: 16 }}>
                <Statistic
                  title="无人机状态"
                  value={selectedDrone.status || 'N/A'}
                  valueStyle={{ 
                    color: getDroneStatusColor(selectedDrone.status) === 'green' ? '#3f8600' : 
                           getDroneStatusColor(selectedDrone.status) === 'blue' ? '#1890ff' :
                           getDroneStatusColor(selectedDrone.status) === 'orange' ? '#faad14' : 
                           getDroneStatusColor(selectedDrone.status) === 'red' ? '#cf1322' : '#000000'
                  }}
                  prefix={<RocketOutlined />}
                />
              </Card>
              
              <Card style={{ marginBottom: 16 }}>
                <Statistic
                  title="电池电量"
                  value={selectedDrone.battery_level ? `${selectedDrone.battery_level.toFixed(1)}%` : 'N/A'}
                  valueStyle={{ 
                    color: getBatteryColor(selectedDrone.battery_level) === 'green' ? '#3f8600' : 
                           getBatteryColor(selectedDrone.battery_level) === 'orange' ? '#faad14' : '#cf1322'
                  }}
                  prefix={<ThunderboltOutlined />}
                />
              </Card>
              
              <Card>
                <Descriptions title="详细信息" column={1} bordered size="small">
                  <Descriptions.Item label="ID">{selectedDrone.id}</Descriptions.Item>
                  <Descriptions.Item label="状态">
                    <Badge 
                      status={getDroneStatusColor(selectedDrone.status)} 
                      text={selectedDrone.status || 'N/A'} 
                    />
                  </Descriptions.Item>
                  <Descriptions.Item label="电池电量">
                    <Badge 
                      color={getBatteryColor(selectedDrone.battery_level)} 
                      text={`${selectedDrone.battery_level ? selectedDrone.battery_level.toFixed(1) : 'N/A'}%`} 
                    />
                  </Descriptions.Item>
                  <Descriptions.Item label="位置">
                    {selectedDrone.position ? (
                      (() => {
                        const pos = Array.isArray(selectedDrone.position) 
                          ? selectedDrone.position 
                          : JSON.parse(selectedDrone.position);
                        return `(${pos[0].toFixed(2)}, ${pos[1].toFixed(2)}, ${pos[2].toFixed(2)})`;
                      })()
                    ) : 'N/A'}
                  </Descriptions.Item>
                  <Descriptions.Item label="速度">
                    {selectedDrone.speed ? `${selectedDrone.speed.toFixed(1)} m/s` : '0.0 m/s'}
                  </Descriptions.Item>
                  <Descriptions.Item label="最后更新">
                    {selectedDrone.timestamp ? new Date(selectedDrone.timestamp).toLocaleString() : 'N/A'}
                  </Descriptions.Item>
                </Descriptions>
              </Card>
            </>
          ) : (
            <Card>
              <div style={{ textAlign: 'center', padding: '20px' }}>
                请选择一个无人机
              </div>
            </Card>
          )}
        </Col>
        
        {/* 地图 */}
        <Col span={16}>
          <Card title="无人机位置与轨迹" style={{ height: '600px' }}>
            <div style={{ height: '100%', width: '100%' }}>
              <MapContainer 
                center={mapCenter} 
                zoom={mapZoom} 
                style={{ height: '100%', width: '100%' }}
                whenCreated={map => { mapRef.current = map; }}
              >
                <TileLayer
                  attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                  url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                />
                
                {/* 显示无人机当前位置 */}
                {selectedDrone && selectedDrone.position && (
                  (() => {
                    const position = Array.isArray(selectedDrone.position) 
                      ? selectedDrone.position 
                      : JSON.parse(selectedDrone.position);
                    return (
                      <Marker position={[position[1], position[0]]}>
                        <Popup>
                          <div>
                            <strong>{selectedDrone.id}</strong><br />
                            状态: {selectedDrone.status}<br />
                            电池: {selectedDrone.battery_level ? `${selectedDrone.battery_level.toFixed(1)}%` : 'N/A'}<br />
                            高度: {position[2].toFixed(1)} m
                          </div>
                        </Popup>
                      </Marker>
                    );
                  })()
                )}
                
                {/* 显示轨迹 */}
                {showTrajectory && trajectory.length > 0 && (
                  <Polyline 
                    positions={trajectory}
                    color="blue"
                    weight={3}
                    opacity={0.7}
                  />
                )}
              </MapContainer>
            </div>
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default DroneMonitor;