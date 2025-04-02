// eslint-disable-next-line no-unused-vars
import moment from 'moment';
import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { 
  Row, Col, Card, DatePicker, Button, Table, Spin, Alert, 
  Select, Empty, Tabs, Statistic
} from 'antd';
import { 
  ReloadOutlined, 
  HistoryOutlined,   
  EnvironmentOutlined
} from '@ant-design/icons';
import { droneApi } from '../services/api';

// 引入地图组件，这里假设使用react-leaflet
import { MapContainer, TileLayer, Marker, Popup, Polyline } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import L from 'leaflet';

// 引入图表组件，这里假设使用echarts-for-react
import ReactECharts from 'echarts-for-react';

// 修复Leaflet默认图标问题
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon-2x.png',
  iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
});

const { Option } = Select;
const { RangePicker } = DatePicker;
const { TabPane } = Tabs;

const DroneHistory = () => {
  // 获取URL参数中的无人机ID
  const { droneId } = useParams();
  
  // 状态管理
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [droneInfo, setDroneInfo] = useState(null);
  const [historyData, setHistoryData] = useState([]);
  const [trajectoryData, setTrajectoryData] = useState([]);
  const [timeRange, setTimeRange] = useState([null, null]);
  const [mapCenter, setMapCenter] = useState([39.9042, 116.4074]); // 默认北京中心
  const [mapZoom, setMapZoom] = useState(12);
  const [maxRecords, setMaxRecords] = useState(100);
  const [samplingInterval, setSamplingInterval] = useState(1.0);
  const mapRef = useRef(null);
  const [activeTab, setActiveTab] = useState('1');

  // 加载历史数据
  const fetchHistoryData = useCallback(async () => {
    if (!droneId) return;
    
    setLoading(true);
    setError(null);
    try {
      // 转换时间范围为仿真时间
      const startTime = timeRange[0] ? timeRange[0].valueOf() : null;
      const endTime = timeRange[1] ? timeRange[1].valueOf() : null;
      // 获取历史状态数据
      const history = await droneApi.getDroneHistory(droneId, startTime, endTime, maxRecords);
      setHistoryData(history);
      
      // 获取轨迹数据
      const trajectory = await droneApi.getDroneTrajectory(droneId, startTime, endTime, samplingInterval);
      
      // 转换轨迹数据格式为地图可用的格式
      const trajectoryPoints = trajectory.map(point => {
        const position = Array.isArray(point.position)
          ? point.position
          : JSON.parse(point.position);
        return {
          position: [position[1], position[0]], // 注意经纬度顺序
          altitude: position[2],
          time: point.sim_time
        };
      });
      
      setTrajectoryData(trajectoryPoints);
      
      // 如果有轨迹点，更新地图中心到第一个点
      if (trajectoryPoints.length > 0) {
        setMapCenter(trajectoryPoints[0].position);
        
        // 如果地图已加载，调整视图以显示整个轨迹
        if (mapRef.current && trajectoryPoints.length > 1) {
          const bounds = L.latLngBounds(trajectoryPoints.map(p => p.position));
          mapRef.current.fitBounds(bounds, { padding: [50, 50] });
        }
      }
    } catch (err) {
      console.error('获取历史数据失败:', err);
      setError('获取历史数据失败，请检查网络连接或刷新页面重试');
    } finally {
      setLoading(false);
    }
  }, [droneId, timeRange, maxRecords, samplingInterval]);

  // 加载无人机信息
  useEffect(() => {
    const fetchDroneInfo = async () => {
      if (!droneId) return;
      
      setLoading(true);
      setError(null);
      try {
        const info = await droneApi.getDrone(droneId);
        setDroneInfo(info);
        
        // 如果有位置数据，更新地图中心
        if (info.position) {
          const position = Array.isArray(info.position) 
            ? info.position 
            : JSON.parse(info.position);
          setMapCenter([position[1], position[0]]); // 注意经纬度顺序
        }
      } catch (err) {
        console.error('获取无人机信息失败:', err);
        setError('获取无人机信息失败，请检查网络连接或刷新页面重试');
      } finally {
        setLoading(false);
      }
    };

    fetchDroneInfo();
  }, [droneId]);

  // 初始加载历史数据
  useEffect(() => {
    fetchHistoryData();
  }, [fetchHistoryData]);

  // 处理时间范围变化
  const handleTimeRangeChange = (dates) => {
    setTimeRange(dates);
  };

  // 处理最大记录数变化
  const handleMaxRecordsChange = (value) => {
    setMaxRecords(value);
  };

  // 处理采样间隔变化
  const handleSamplingIntervalChange = (value) => {
    setSamplingInterval(value);
  };

  // 处理刷新按钮点击
  const handleRefresh = () => {
    fetchHistoryData();
  };

  // 历史数据表格列定义
  const historyColumns = [
    {
      title: '时间',
      dataIndex: 'sim_time',
      key: 'sim_time',
      render: (time) => formatSimTime(time)
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (status) => {
        let color = 'green';
        if (status === 'idle') color = 'blue';
        else if (status === 'charging') color = 'orange';
        else if (status === 'error') color = 'red';
        
        return <span style={{ color }}>{status}</span>;
      }
    },
    {
      title: '电池电量',
      dataIndex: 'battery_level',
      key: 'battery_level',
      render: (battery) => `${battery ? battery.toFixed(1) : 'N/A'}%`
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
      render: (speed) => `${speed ? speed.toFixed(1) : '0.0'} m/s`
    }
  ];

  // 格式化仿真时间
  const formatSimTime = (time) => {
    if (!time && time !== 0) return 'N/A';
    const hours = Math.floor(time / 3600);
    const minutes = Math.floor((time % 3600) / 60);
    const seconds = Math.floor(time % 60);
    return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
  };

  // 准备电池电量图表选项
  const getBatteryChartOption = () => {
    // 提取时间和电池电量数据
    const times = historyData.map(item => formatSimTime(item.sim_time));
    const batteryLevels = historyData.map(item => item.battery_level);
    
    return {
      title: {
        text: '电池电量历史',
        left: 'center'
      },
      tooltip: {
        trigger: 'axis',
        formatter: function(params) {
          const time = params[0].name;
          const value = params[0].value;
          return `时间: ${time}<br/>电池电量: ${value.toFixed(1)}%`;
        }
      },
      xAxis: {
        type: 'category',
        data: times, // 添加 x 轴数据
        axisLabel: {
          rotate: 45, // 控制标签旋转角度
          interval: 'auto', // 自动控制标签间隔，防止重叠
          formatter: function (value) { // 可选：如果标签过长，可以截断
               return value.length > 10 ? value.substring(0, 10) + '...' : value;
          }
        }
      },
      yAxis: {
        type: 'value',
        name: '电池电量 (%)',
        min: 0,
        max: 100
      },
      series: [{
        name: '电池电量',
        type: 'line',
        data: batteryLevels,
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [{
              offset: 0, color: 'rgba(0, 128, 0, 0.7)' // 绿色
            }, {
              offset: 1, color: 'rgba(0, 128, 0, 0.1)'
            }]
          }
        },
        itemStyle: {
          color: 'green'
        }
      }]
    };
  };

  // 准备高度图表选项
  const getAltitudeChartOption = () => {
    // 提取时间和高度数据
    const times = trajectoryData.map(item => formatSimTime(item.time));
    const altitudes = trajectoryData.map(item => item.altitude);
    
    return {
      title: {
        text: '飞行高度历史',
        left: 'center'
      },
      tooltip: {
        trigger: 'axis',
        formatter: function(params) {
          const time = params[0].name;
          const value = params[0].value;
          return `时间: ${time}<br/>高度: ${value.toFixed(1)} m`;
        }
      },
      xAxis: {
        type: 'category',
        data: times,
        axisLabel: {
          rotate: 45
        }
      },
      yAxis: {
        type: 'value',
        name: '高度 (m)'
      },
      series: [{
        name: '高度',
        type: 'line', // 修正点 1：添加 type: 'line'
        data: altitudes, // 修正点 1：添加 data
        smooth: true, // 可选：平滑线条
        areaStyle: { // 修正点 1：确保 areaStyle 在正确的位置
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [{
              offset: 0, color: 'rgba(30, 144, 255, 0.7)' // 蓝色 dodgerblue
            }, {
              offset: 1, color: 'rgba(30, 144, 255, 0.1)'
            }]
          }
        },
        itemStyle: {
          color: 'dodgerblue' // 使用更具体的蓝色名称或保持 'blue'
        },
        lineStyle: { // 可选：定义线条样式
            color: 'darkblue',
            width: 2
        },
        emphasis: { // 可选：鼠标悬浮时的高亮样式
          focus: 'series',
           itemStyle: {
             borderColor: 'black',
             borderWidth: 1
          }
        }
      }]
    };
  };

  if (loading && !droneInfo) {
    return (
      <div style={{ textAlign: 'center', padding: '50px' }}>
        <Spin size="large" tip="加载中..." />
      </div>
    );
  }

  return (
    <div className="drone-history">
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

      {/* 标题和无人机信息 */}
      <Card style={{ marginBottom: 16 }}>
        <Row gutter={16}>
          <Col span={12}>
            <Statistic
              title="无人机ID"
              value={droneId || 'N/A'}
              prefix={<EnvironmentOutlined />}
            />
          </Col>
          <Col span={12}>
            <Statistic
              title="记录数量"
              value={historyData.length}
              prefix={<HistoryOutlined />}
            />
          </Col>
        </Row>
      </Card>

      {/* 查询控制 */}
      <Card style={{ marginBottom: 16 }}>
        <Row gutter={16} align="middle">
          <Col span={8}>
            <div style={{ marginBottom: 8 }}>时间范围:</div>
            <RangePicker 
              showTime 
              format="YYYY-MM-DD HH:mm:ss"
              onChange={handleTimeRangeChange}
              value={timeRange}
              style={{ width: '100%' }}
            />
          </Col>
          <Col span={5}>
            <div style={{ marginBottom: 8 }}>最大记录数:</div>
            <Select
              value={maxRecords}
              onChange={handleMaxRecordsChange}
              style={{ width: '100%' }}
            >
              <Option value={50}>50</Option>
              <Option value={100}>100</Option>
              <Option value={200}>200</Option>
              <Option value={500}>500</Option>
              <Option value={1000}>1000</Option>
            </Select>
          </Col>
          <Col span={5}>
            <div style={{ marginBottom: 8 }}>采样间隔 (秒):</div>
            <Select
              value={samplingInterval}
              onChange={handleSamplingIntervalChange}
              style={{ width: '100%' }}
            >
              <Option value={0.1}>0.1</Option>
              <Option value={0.5}>0.5</Option>
              <Option value={1.0}>1.0</Option>
              <Option value={5.0}>5.0</Option>
              <Option value={10.0}>10.0</Option>
            </Select>
          </Col>
          <Col span={6} style={{ textAlign: 'right' }}>
            <Button 
              type="primary" 
              icon={<ReloadOutlined />} 
              onClick={handleRefresh}
              loading={loading}
              style={{ marginTop: 24 }}
            >
              查询
            </Button>
          </Col>
        </Row>
      </Card>

      {/* 数据展示区域 */}
      <Tabs activeKey={activeTab} onChange={setActiveTab}>
        <TabPane tab="数据表格" key="1">
          <Card bodyStyle={{ padding: 0 }}>
            <Table 
              dataSource={historyData} 
              columns={historyColumns} 
              rowKey={(record, index) => index}
              pagination={{ pageSize: 10 }}
              loading={loading}
              scroll={{ x: 'max-content' }}
            />
          </Card>
        </TabPane>
        <TabPane tab="轨迹地图" key="2">
          <Card style={{ height: '500px' }}>
            {trajectoryData.length > 0 ? (
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
                  
                  {/* 显示轨迹起点 */}
                  {trajectoryData.length > 0 && (
                    <Marker position={trajectoryData[0].position}>
                      <Popup>
                        起点<br />
                        时间: {formatSimTime(trajectoryData[0].time)}<br />
                        高度: {trajectoryData[0].altitude.toFixed(1)} m
                      </Popup>
                    </Marker>
                  )}
                  
                  {/* 显示轨迹终点 */}
                  {trajectoryData.length > 1 && (
                    <Marker position={trajectoryData[trajectoryData.length - 1].position}>
                      <Popup>
                        终点<br />
                        时间: {formatSimTime(trajectoryData[trajectoryData.length - 1].time)}<br />
                        高度: {trajectoryData[trajectoryData.length - 1].altitude.toFixed(1)} m
                      </Popup>
                    </Marker>
                  )}
                  
                  {/* 显示轨迹线 */}
                  {trajectoryData.length > 1 && (
                    <Polyline 
                      positions={trajectoryData.map(p => p.position)}
                      color="blue"
                      weight={3}
                      opacity={0.7}
                    />
                  )}
                </MapContainer>
              </div>
            ) : (
              <Empty description="暂无轨迹数据" />
            )}
          </Card>
        </TabPane>
        <TabPane tab="图表分析" key="3">
          <Row gutter={16}>
            <Col span={24} style={{ marginBottom: 16 }}>
              <Card title="电池电量历史">
                {historyData.length > 0 ? (
                  <ReactECharts 
                    option={getBatteryChartOption()} 
                    style={{ height: '300px' }}
                  />
                ) : (
                  <Empty description="暂无电池数据" />
                )}
              </Card>
            </Col>
            <Col span={24}>
              <Card title="飞行高度历史">
                {trajectoryData.length > 0 ? (
                  <ReactECharts 
                    option={getAltitudeChartOption()} 
                    style={{ height: '300px' }}
                  />
                ) : (
                  <Empty description="暂无高度数据" />
                )}
              </Card>
            </Col>
          </Row>
        </TabPane>
      </Tabs>
    </div>
  );
};

export default DroneHistory;
