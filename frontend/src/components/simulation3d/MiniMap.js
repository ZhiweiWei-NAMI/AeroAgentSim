import React, { useRef, useState, useEffect } from 'react';
import { MapContainer, TileLayer, Marker, Popup } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

// 地图样式配置
const mapStyles = {
  standard: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
  satellite: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  dark: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png'
};

// 自定义图标创建函数
const createCustomIcon = (color, type) => {
  return new L.Icon({
    iconUrl: 'https://unpkg.com/leaflet@1.7.1/dist/images/marker-icon.png',
    iconSize: [25, 41],
    iconAnchor: [12, 41],
    popupAnchor: [1, -34],
    shadowUrl: 'https://unpkg.com/leaflet@1.7.1/dist/images/marker-shadow.png',
    shadowSize: [41, 41],
    className: `custom-icon-${type}`,
    iconStyle: `background-color: ${color}; border-radius: 50%;`,
  });
};

// 小地图组件
const MiniMap = ({ drones, vehicles, cameraPosition, cameraTarget, mapStyle = 'standard', onMapClick, onMapDrag, onMapCenterChange, zoom: externalZoom, baseCenter = [39.9042, 116.4074] }) => {
  const mapRef = useRef();
  const [center, setCenter] = useState(baseCenter); // 使用传入的基准中心点
  const [zoom, setZoom] = useState(externalZoom || 13);
  const [map, setMap] = useState(null);
  const isDraggingRef = useRef(false);
  const lastCenterRef = useRef(baseCenter);
  const [allMarkers, setAllMarkers] = useState([]); // 存储所有标记的位置
  
  // 将3D坐标转换为地理坐标（回到简单但可靠的转换方法）
  const convertTo2DCoords = (position) => {
    // 使用简单的线性映射，但调整比例因子使其更合适
    // 假设原点(0,0,0)对应中心点，每单位对应0.0001经纬度
    const scaleFactor = 0.0001; // 可以根据需要调整这个比例因子
    const lat = baseCenter[0] + position[2] * scaleFactor;
    const lng = baseCenter[1] + position[0] * scaleFactor;
    return [lat, lng];
  };
  
  // 将地理坐标转换为3D坐标
  const convertTo3DCoords = (latLng) => {
    // 使用与上面相对应的反向转换
    const scaleFactor = 0.0001;
    const x = (latLng[1] - baseCenter[1]) / scaleFactor;
    const z = (latLng[0] - baseCenter[0]) / scaleFactor;
    return [x, 0, z]; // y坐标设为0（地面高度）
  };
  
  // 处理"抵达该视角"按钮点击
  const handleGoToPosition = (position3D) => {
    if (onMapClick) {
      onMapClick(position3D);
    }
  };
  
  // 保存地图实例引用
  // 清理定时器
  useEffect(() => {
    return () => {
      if (mapRef.current && mapRef.current.updateInterval) {
        clearInterval(mapRef.current.updateInterval);
      }
    };
  }, []);
  
  useEffect(() => {
    if (mapRef.current) {
      setMap(mapRef.current);
    }
  }, []);
  
  // 响应外部zoom变化
  useEffect(() => {
    if (externalZoom && externalZoom !== zoom) {
      setZoom(externalZoom);
      if (map) {
        map.setZoom(externalZoom);
      }
    }
  }, [externalZoom, map, zoom]);

  // 收集所有标记的位置
  useEffect(() => {
    const markers = [];
    
    // 收集无人机位置
    drones.forEach(drone => {
      try {
        const position = convertTo2DCoords(drone.position);
        markers.push({
          id: `drone-${drone.id}`,
          position,
          type: 'drone'
        });
      } catch (error) {
        console.error(`处理无人机 ${drone.id} 位置时出错:`, error);
      }
    });
    
    // 收集车辆位置
    vehicles.forEach(vehicle => {
      try {
        const position = convertTo2DCoords(vehicle.position);
        markers.push({
          id: `vehicle-${vehicle.id}`,
          position,
          type: 'vehicle'
        });
      } catch (error) {
        console.error(`处理车辆 ${vehicle.id} 位置时出错:`, error);
      }
    });
    
    setAllMarkers(markers);
  }, [drones, vehicles, baseCenter]); // 添加baseCenter作为依赖项，确保在地图中心变化时更新标记
  
  // 更新摄像机位置标记和地图中心
  useEffect(() => {
    if (map && cameraPosition) {
      try {
        // 计算摄像机的地理坐标
        const cameraLatLng = convertTo2DCoords([cameraPosition.x, cameraPosition.y, cameraPosition.z]);
        
        // 只有当用户没有手动拖动地图时，才自动更新地图中心
        if (!isDraggingRef.current) {
          // 更新状态
          setCenter(cameraLatLng);
          console.log('MiniMap 中心位置更新:', cameraLatLng);
          
          // 将当前相机位置转回3D坐标
          const position3D = [cameraPosition.x, cameraPosition.y, cameraPosition.z];
          
          // 调用中心点变化回调，更新大地图相机位置
          if (onMapCenterChange) {
            console.log('MiniMap 调用onMapCenterChange更新相机:', position3D);
            onMapCenterChange(position3D);
          }
          
          // 确保地图能够显示所有标记
          if (allMarkers.length > 0) {
            // 创建一个边界，包含所有标记和相机位置
            const bounds = L.latLngBounds([cameraLatLng]);
            
            // 添加所有标记到边界
            allMarkers.forEach(marker => {
              bounds.extend(marker.position);
            });
            
            // 设置地图视图以显示所有标记
            // 使用较小的padding，确保标记不会太靠近边缘
            map.fitBounds(bounds, {
              padding: [30, 30],
              maxZoom: zoom, // 限制最大缩放级别
              animate: true,
              duration: 0.5
            });
          } else {
            // 如果没有标记，则只更新相机位置
            map.setView(cameraLatLng, zoom, { animate: true, duration: 0.5 });
          }
        }
      } catch (error) {
        console.error("更新摄像机位置时出错:", error);
      }
    }
  }, [cameraPosition, map, zoom, allMarkers]);
  
  // 处理地图点击和拖动事件
  const handleMapClick = (e) => {
    if (isDraggingRef.current) {
      // 如果是拖动结束，不触发点击事件
      isDraggingRef.current = false;
      return;
    }
    
    if (onMapClick) {
      // 将点击的地理坐标转换为3D坐标
      const coords3D = convertTo3DCoords([e.latlng.lat, e.latlng.lng]);
      onMapClick(coords3D);
    }
  };
  
  // 获取不同类型的图标
  const getMarkerIcon = (type) => {
    switch(type) {
      case 'inspector': return createCustomIcon('red', 'inspector');
      case 'delivery': return createCustomIcon('blue', 'delivery');
      case 'car': return createCustomIcon('green', 'car');
      case 'truck': return createCustomIcon('purple', 'truck');
      case 'van': return createCustomIcon('orange', 'van');
      default: return createCustomIcon('gray', 'default');
    }
  };
  
  // 自定义弹出框内容样式
  const popupContentStyle = {
    fontFamily: 'Arial, sans-serif',
    fontSize: '12px',
    lineHeight: '1.4'
  };
  
  // 自定义按钮样式
  const buttonStyle = {
    display: 'block',
    width: '100%',
    padding: '6px 0',
    marginTop: '8px',
    backgroundColor: '#4CAF50',
    color: 'white',
    border: 'none',
    borderRadius: '4px',
    cursor: 'pointer',
    textAlign: 'center',
    fontSize: '12px',
    fontWeight: 'bold'
  };
  
  return (
    <div style={{
      position: 'absolute',
      top: 10,
      left: 10,
      width: 200,
      height: 200,
      zIndex: 1000,
      border: '2px solid rgba(0,0,0,0.2)',
      borderRadius: '4px',
      overflow: 'hidden'
    }}>
      <MapContainer
        center={center}
        zoom={zoom}
        style={{ height: '100%', width: '100%' }}
        zoomControl={false}
        attributionControl={false}
        ref={mapRef}
        whenCreated={(mapInstance) => {
          setMap(mapInstance);
          
          // 添加拖动开始和结束事件监听
          mapInstance.on('dragstart', () => {
            isDraggingRef.current = true;
          });
          
          // 监听地图移动结束事件
          mapInstance.on('moveend', () => {
            if (isDraggingRef.current) { // 检查是否是拖动结束
              const newCenter = mapInstance.getCenter();
              lastCenterRef.current = [newCenter.lat, newCenter.lng]; // 更新最后中心点
              console.log('MiniMap 拖动后中心位置:', [newCenter.lat, newCenter.lng]);
              
              // 处理拖动和更新标记
              // 转换为3D坐标
              const center3D = convertTo3DCoords([newCenter.lat, newCenter.lng]);
              // 调用拖动回调
              if (onMapDrag) {
                onMapDrag(center3D);
              }
              
              // 调用中心点变化回调
              if (onMapCenterChange) {
                onMapCenterChange(center3D);
              }

              // 强制更新标记位置
              const updatedMarkers = [];
              drones.forEach(drone => {
                try {
                  const position = convertTo2DCoords(drone.position);
                  updatedMarkers.push({
                    id: `drone-${drone.id}`,
                    position,
                    type: 'drone'
                  });
                } catch (error) {
                  console.error(`处理无人机 ${drone.id} 位置时出错:`, error);
                }
              });

              vehicles.forEach(vehicle => {
                try {
                  const position = convertTo2DCoords(vehicle.position);
                  updatedMarkers.push({
                    id: `vehicle-${vehicle.id}`,
                    position,
                    type: 'vehicle'
                  });
                } catch (error) {
                  console.error(`处理车辆 ${vehicle.id} 位置时出错:`, error);
                }
              });
              setAllMarkers(updatedMarkers); // 更新标记状态
              
              // 重置拖动状态
              isDraggingRef.current = false;
            }
          });
          
          // 添加一个定时器，定期更新标记位置，确保即使在锁定状态下也能正确显示
          const updateInterval = setInterval(() => {
            // 更新标记，无论相机状态如何
            const updatedMarkers = [];
            drones.forEach(drone => {
              try {
                const position = convertTo2DCoords(drone.position);
                updatedMarkers.push({
                  id: `drone-${drone.id}`,
                  position,
                  type: 'drone'
                });
              } catch (error) {
                console.error(`处理无人机 ${drone.id} 位置时出错:`, error);
              }
            });

            vehicles.forEach(vehicle => {
              try {
                const position = convertTo2DCoords(vehicle.position);
                updatedMarkers.push({
                  id: `vehicle-${vehicle.id}`,
                  position,
                  type: 'vehicle'
                });
              } catch (error) {
                console.error(`处理车辆 ${vehicle.id} 位置时出错:`, error);
              }
            });
            
            // 添加调试日志，查看车辆位置更新
            if (vehicles.length > 0) {
              // console.log('MiniMap 更新车辆位置:', vehicles[0].id, vehicles[0].position);
            }
            
            setAllMarkers(updatedMarkers);
          }, 100); // 每0.1秒更新一次 (改为100ms)
          
          // 清理函数
          mapRef.current.updateInterval = updateInterval;
        }}
        onClick={handleMapClick}
      >
        <TileLayer
          url={mapStyles[mapStyle] || mapStyles.standard}
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
        />
        
        {/* 摄像机位置标记 - 使用自定义图标显示方向 */}
        {cameraPosition && (
          <Marker
            position={convertTo2DCoords([cameraPosition.x, cameraPosition.y, cameraPosition.z])}
            icon={createCustomIcon('#ff0000', 'camera')}
            rotationAngle={cameraTarget ? Math.atan2(
              cameraTarget.z - cameraPosition.z,
              cameraTarget.x - cameraPosition.x
            ) * (180 / Math.PI) : 0}
          >
            <Popup>
              <div style={popupContentStyle}>
                <div>当前视角位置</div>
                <div>X: {cameraPosition.x.toFixed(1)}</div>
                <div>Y: {cameraPosition.y.toFixed(1)}</div>
                <div>Z: {cameraPosition.z.toFixed(1)}</div>
              </div>
            </Popup>
          </Marker>
        )}
        
        {/* 无人机标记 */}
        {drones.map(drone => {
          try {
            // 计算2D位置
            const position = convertTo2DCoords(drone.position);
            
            return (
              <Marker
                key={`map-drone-${drone.id}`}
                position={position}
                icon={getMarkerIcon(drone.type)}
              >
                <Popup>
                  <div style={popupContentStyle}>
                    <div>ID: {drone.id}</div>
                    <div>类型: {drone.type}</div>
                    <div>电量: {(drone.battery_level || drone.battery || 100).toFixed(1)}%</div>
                    <div>位置: ({drone.position[0].toFixed(1)}, {drone.position[1].toFixed(1)}, {drone.position[2].toFixed(1)})</div>
                    <button
                      style={buttonStyle}
                      onClick={() => handleGoToPosition(drone.position)}
                    >
                      抵达该视角
                    </button>
                  </div>
                </Popup>
              </Marker>
            );
          } catch (error) {
            console.error(`渲染无人机 ${drone.id} 时出错:`, error);
            return null;
          }
        })}
        
        {/* 车辆标记 - 使用 allMarkers 中的车辆数据 */}
        {allMarkers.filter(marker => marker.id.startsWith('vehicle-')).map(marker => {
          try {
            // 从 vehicles 数组中找到对应的车辆数据
            const vehicleId = marker.id.replace('vehicle-', '');
            const vehicle = vehicles.find(v => v.id === vehicleId);
            
            if (!vehicle) return null;
            
            return (
              <Marker
                key={marker.id}
                position={marker.position}
                icon={getMarkerIcon(vehicle.type)}
              >
                <Popup>
                  <div style={popupContentStyle}>
                    <div>ID: {vehicle.id}</div>
                    <div>类型: {vehicle.type}</div>
                    <div>速度: {vehicle.speed.toFixed(1)} km/h</div>
                    <div>位置: ({vehicle.position[0].toFixed(1)}, {vehicle.position[1].toFixed(1)}, {vehicle.position[2].toFixed(1)})</div>
                    <div>地图位置: ({marker.position[0].toFixed(5)}, {marker.position[1].toFixed(5)})</div>
                    <button
                      style={buttonStyle}
                      onClick={() => handleGoToPosition(vehicle.position)}
                    >
                      抵达该视角
                    </button>
                  </div>
                </Popup>
              </Marker>
            );
          } catch (error) {
            console.error(`渲染车辆标记 ${marker.id} 时出错:`, error);
            return null;
          }
        })}
      </MapContainer>
    </div>
  );
};

// 添加默认属性
MiniMap.defaultProps = {
  onMapClick: null,
  onMapDrag: null,
  onMapCenterChange: null,
  zoom: 13,
  baseCenter: [39.9042, 116.4074] // 默认北京中心
};

export default MiniMap;