import React, { useState, useEffect } from 'react';
import Simulation3DView from '../components/Simulation3DView';

// 3D地图视图页面
const Simulation3DMapView = () => {
  const [mapStyle, setMapStyle] = useState('standard');
  const [timeOfDay, setTimeOfDay] = useState(12);
  const [weatherType, setWeatherType] = useState('all');
  
  // 地图配置状态
  const [mapCenter, setMapCenter] = useState([39.9042, 116.4074]); // 默认北京中心
  const [mapRadius, setMapRadius] = useState(0.01); // 默认1公里半径
  const [shouldLoadMap, setShouldLoadMap] = useState(false);
  
  // 临时存储用户输入的值
  const [tempLat, setTempLat] = useState('39.9042');
  const [tempLng, setTempLng] = useState('116.4074');
  const [tempRadius, setTempRadius] = useState('0.01');
  
  // 从localStorage加载地图配置
  useEffect(() => {
    const storedLat = localStorage.getItem('mapCenterLat');
    const storedLng = localStorage.getItem('mapCenterLng');
    const storedRadius = localStorage.getItem('mapRadius');
    
    if (storedLat && storedLng) {
      const lat = parseFloat(storedLat);
      const lng = parseFloat(storedLng);
      setMapCenter([lat, lng]);
      
      // 更新临时存储的值
      setTempLat(storedLat);
      setTempLng(storedLng);
    }
    
    if (storedRadius) {
      const radius = parseFloat(storedRadius);
      setMapRadius(radius);
      setTempRadius(storedRadius);
    }
    
    // 自动应用地图设置
    setShouldLoadMap(true);
  }, []);
  
  // 监听localStorage变化
  useEffect(() => {
    // 创建一个函数来处理存储事件
    const handleStorageChange = () => {
      const storedLat = localStorage.getItem('mapCenterLat');
      const storedLng = localStorage.getItem('mapCenterLng');
      const storedRadius = localStorage.getItem('mapRadius');
      
      if (storedLat && storedLng) {
        const lat = parseFloat(storedLat);
        const lng = parseFloat(storedLng);
        setMapCenter([lat, lng]);
        setTempLat(storedLat);
        setTempLng(storedLng);
      }
      
      if (storedRadius) {
        const radius = parseFloat(storedRadius);
        setMapRadius(radius);
        setTempRadius(storedRadius);
      }
      
      // 自动应用新的地图设置
      setShouldLoadMap(true);
    };
    
    // 添加事件监听器
    window.addEventListener('storage', handleStorageChange);
    
    // 清理函数
    return () => {
      window.removeEventListener('storage', handleStorageChange);
    };
  }, []);
  
  // 应用地图设置
  const applyMapSettings = () => {
    const lat = parseFloat(tempLat);
    const lng = parseFloat(tempLng);
    const radius = parseFloat(tempRadius);
    
    if (isNaN(lat) || isNaN(lng) || isNaN(radius)) {
      alert('请输入有效的经纬度和半径值');
      return;
    }
    
    // 更新本地状态
    setMapCenter([lat, lng]);
    setMapRadius(radius);
    setShouldLoadMap(true);
    
    // 同步到localStorage以便与SimulationControl共享
    localStorage.setItem('mapCenterLat', lat.toString());
    localStorage.setItem('mapCenterLng', lng.toString());
    localStorage.setItem('mapRadius', radius.toString());
  };
  
  // 地图样式选项
  const mapStyleOptions = [
    { value: 'standard', label: '标准' },
    { value: 'satellite', label: '卫星' },
    { value: 'dark', label: '暗黑' }
  ];
  
  // 天气类型选项
  const weatherTypeOptions = [
    { value: 'all', label: '全部' },
    { value: 'rain', label: '雨' },
    { value: 'wind', label: '风' },
    { value: 'fog', label: '雾' },
    { value: 'storm', label: '暴风雨' },
    { value: 'sunny', label: '晴天' }
  ];
  
  return (
    <div style={{ height: 'calc(100vh - 64px)', width: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* 控制面板 */}
      <div style={{
        padding: '10px',
        background: '#f8f8f8',
        borderBottom: '1px solid #ddd',
        display: 'flex',
        flexDirection: 'column',
        gap: '10px'
      }}>
        {/* 第一行控制 - 基本设置 */}
        <div style={{ display: 'flex', gap: '20px', alignItems: 'center', flexWrap: 'wrap' }}>
          {/* 地图样式选择器 */}
          <div>
            <label style={{ marginRight: '8px' }}>地图样式:</label>
            <select
              value={mapStyle}
              onChange={(e) => setMapStyle(e.target.value)}
              style={{ padding: '4px 8px' }}
            >
              {mapStyleOptions.map(option => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          
          {/* 时间选择器 */}
          <div>
            <label style={{ marginRight: '8px' }}>时间 (小时):</label>
            <input
              type="range"
              min="0"
              max="23"
              value={timeOfDay}
              onChange={(e) => setTimeOfDay(parseInt(e.target.value))}
              style={{ width: '100px', verticalAlign: 'middle' }}
            />
            <span style={{ marginLeft: '8px' }}>{timeOfDay}:00</span>
          </div>
          
          {/* 天气类型选择器 */}
          <div>
            <label style={{ marginRight: '8px' }}>天气类型:</label>
            <select
              value={weatherType}
              onChange={(e) => setWeatherType(e.target.value)}
              style={{ padding: '4px 8px' }}
            >
              {weatherTypeOptions.map(option => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
        </div>
        
        {/* 第二行控制 - 地图配置 */}
        <div style={{
          display: 'flex',
          gap: '15px',
          alignItems: 'center',
          flexWrap: 'wrap',
          padding: '8px',
          backgroundColor: '#eef2f5',
          borderRadius: '4px'
        }}>
          <div>
            <label style={{ marginRight: '8px', fontWeight: 'bold' }}>地图配置:</label>
          </div>
          
          <div>
            <label style={{ marginRight: '4px' }}>纬度:</label>
            <input
              type="text"
              value={tempLat}
              onChange={(e) => setTempLat(e.target.value)}
              style={{ width: '80px', padding: '4px' }}
            />
          </div>
          
          <div>
            <label style={{ marginRight: '4px' }}>经度:</label>
            <input
              type="text"
              value={tempLng}
              onChange={(e) => setTempLng(e.target.value)}
              style={{ width: '80px', padding: '4px' }}
            />
          </div>
          
          <div>
            <label style={{ marginRight: '4px' }}>半径(km):</label>
            <input
              type="text"
              value={tempRadius}
              onChange={(e) => setTempRadius(e.target.value)}
              style={{ width: '60px', padding: '4px' }}
            />
          </div>
          
          <button
            onClick={applyMapSettings}
            style={{
              padding: '6px 12px',
              backgroundColor: '#4CAF50',
              color: 'white',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer'
            }}
          >
            应用地图设置
          </button>
        </div>
      </div>
      
      {/* 3D视图 */}
      <div style={{ flex: 1, position: 'relative' }}>
        <Simulation3DView
          mapStyle={mapStyle}
          timeOfDay={timeOfDay}
          weatherType={weatherType}
          center={mapCenter}
          radius={mapRadius}
          shouldLoadMap={shouldLoadMap}
        />
      </div>
    </div>
  );
};

export default Simulation3DMapView;