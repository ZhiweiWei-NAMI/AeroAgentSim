import React, { Suspense, useState, useCallback, useRef, useEffect } from 'react';
import { Canvas } from '@react-three/fiber';
import { OrbitControls, Sky, Environment } from '@react-three/drei';
import * as THREE from 'three';
import 'leaflet/dist/leaflet.css';

// 导入子组件
import {
  CityModel,
  Drone,
  Vehicle,
  WeatherEffect,
  MiniMap,
  CameraController,
  VehicleMobilityManager,
  DroneMobilityManager
} from './simulation3d';

// 主3D模拟视图组件
const Simulation3DView = ({
  drones = [],
  vehicles = [],
  weatherEvents = [],
  mapStyle = 'standard',
  timeOfDay = 12,
  weatherType = 'all',
  center = [39.9042, 116.4074], // 默认北京中心
  radius = 1, // 默认1公里半径
}) => {
  const [cameraState, setCameraState] = useState(null);
  const [sceneScale, setSceneScale] = useState(1);
  const cameraRef = useRef(null);
  const controlsRef = useRef(null);
  
  // 添加状态来跟踪是否使用示例数据，避免在外部数据变化时频繁切换
  const [useExampleVehicles, setUseExampleVehicles] = useState(true);
  const [useExampleDrones, setUseExampleDrones] = useState(true);

  // 初始示例车辆数据 (保持不变，用于重置)
  const initialExampleVehicles = React.useMemo(() => [], []);

  // 使用 state 管理示例车辆，以便更新
  const [dynamicExampleVehicles, setDynamicExampleVehicles] = useState(initialExampleVehicles);

  // 注意：车辆移动逻辑现在由 VehicleMobilityManager 处理
  
  // 处理相机变化
  const handleCameraChange = useCallback((cameraData) => {
    setCameraState(cameraData);
  }, []);
  
  // 移动相机到指定的3D位置
  const moveCameraTo = useCallback((position3D, height = null) => {
    if (controlsRef.current && cameraRef.current) {
      // 移动相机目标点到点击位置
      controlsRef.current.target.set(position3D[0], position3D[1], position3D[2]);
      
      // 计算新的相机位置
      let newPosition;
      if (height !== null) {
        // 如果指定了高度，则将相机放置在目标上方指定高度处
        newPosition = new THREE.Vector3(position3D[0], position3D[1] + height, position3D[2]);
      } else {
        // 否则保持相对距离
        const currentPos = cameraRef.current.position.clone();
        const targetPos = new THREE.Vector3(position3D[0], position3D[1], position3D[2]);
        const direction = currentPos.clone().sub(controlsRef.current.target);
        newPosition = targetPos.clone();
      }
      // 直接设置相机位置，不使用lerp平滑过渡
      cameraRef.current.position.copy(newPosition);
      
      // 更新控制器
      controlsRef.current.update();
      
      // 更新相机状态以便小地图同步
      if (cameraRef.current && controlsRef.current) {
        const position = cameraRef.current.position.clone();
        const target = controlsRef.current.target.clone();
        setCameraState({
          position,
          target,
          zoom: cameraState?.zoom || 13
        });
      }
    }
  }, [controlsRef, cameraRef, cameraState]);
    

  // 处理车辆位置更新
  const handleVehiclesUpdate = useCallback((updatedVehicles) => {
    setDynamicExampleVehicles(updatedVehicles);
  }, []);

  // 示例无人机数据
  const exampleDrones = [];
  
  // 状态管理示例无人机
  const [dynamicExampleDrones, setDynamicExampleDrones] = useState(exampleDrones);
  
  // 处理无人机位置更新
  const handleDronesUpdate = useCallback((updatedDrones) => {
    console.log('Simulation3DView: 更新无人机位置', updatedDrones.length);
    setDynamicExampleDrones(updatedDrones);
  }, []);
  
  // 处理agent点击事件
  const handleAgentClick = useCallback((position) => {
    // 将相机移动到agent上方100个单位处
    moveCameraTo(position, 100);
  }, [moveCameraTo]);
  
  // 处理小地图点击
  const handleMapClick = useCallback((position3D) => {
    moveCameraTo(position3D, 100);
  }, [moveCameraTo]);
  
  // 处理小地图拖动
  const handleMapDrag = useCallback((position3D) => {
    moveCameraTo(position3D, 100);
  }, [moveCameraTo]);
  
  // 处理小地图中心点变化
  const handleMapCenterChange = useCallback((position3D) => {
    // 移动相机目标点和相机位置
    if (controlsRef.current && cameraRef.current) {
      console.log('Simulation3DView: 接收到小地图中心位置更新:', position3D);
      
      // 移动相机目标点到新的中心位置
      controlsRef.current.target.set(position3D[0], position3D[1], position3D[2]);
      
      // 保持相机与目标点的相对位置不变
      const currentPos = cameraRef.current.position.clone();
      const currentTarget = controlsRef.current.target.clone();
      const offset = currentPos.clone().sub(currentTarget);
      
      // 计算新的相机位置
      const newPosition = new THREE.Vector3(
        position3D[0] + offset.x,
        position3D[1] + offset.y,
        position3D[2] + offset.z
      );
      
      // 直接设置相机位置，不使用lerp平滑过渡，确保立即更新
      cameraRef.current.position.copy(newPosition);
      
      // 更新控制器
      controlsRef.current.update();
      
      // 更新相机状态以便小地图同步
      setCameraState({
        position: cameraRef.current.position.clone(),
        target: controlsRef.current.target.clone(),
        zoom: cameraState?.zoom || 13
      });
      
      console.log('Simulation3DView: 相机位置已更新到:', newPosition);
    }
  }, [controlsRef, cameraRef, cameraState]);
  
  // 过滤气象事件
  const filteredWeatherEvents = weatherEvents.filter(event => {
    if (weatherType === 'all') return true;
    return event.condition === weatherType;
  });
  
  // 根据时间调整天空颜色
  const getSkyProps = () => {
    // 根据时间调整太阳位置
    const sunPosition = [
      100 * Math.cos((timeOfDay / 24) * Math.PI * 2),
      100 * Math.sin((timeOfDay / 24) * Math.PI * 2),
      100
    ];
    
    // 夜间模式
    const isNight = timeOfDay < 6 || timeOfDay > 18;
    
    return {
      sunPosition,
      turbidity: isNight ? 10 : 10,
      rayleigh: isNight ? 3 : 0.5,
      mieCoefficient: isNight ? 0.1 : 0.005,
      mieDirectionalG: 0.7,
      inclination: 0.49,
      azimuth: 0.25,
    };
  };
  

  
  // 示例气象数据
  const exampleWeather = [
    { id: 'w1', position: [10, 10, 10], condition: 'rain', intensity: 0.7 },
    { id: 'w2', position: [-10, 15, -5], condition: 'wind', intensity: 0.5 },
    { id: 'w3', position: [0, 5, -15], condition: 'fog', intensity: 0.3 },
  ];
  
  // 根据外部数据是否存在，更新是否使用示例数据的状态
  useEffect(() => {
    // 只在初始化和外部数据变化时更新状态，避免频繁切换
    if (vehicles.length > 0) {
      setUseExampleVehicles(false);
    } else if (vehicles.length === 0 && !useExampleVehicles) {
      // 只有当外部数据变为空且当前不使用示例数据时才切换回示例数据
      setUseExampleVehicles(true);
    }
    if (drones.length > 0) {
      setUseExampleDrones(false);
    } else if (drones.length === 0 && !useExampleDrones) {
      setUseExampleDrones(true);
    }
  }, [vehicles.length, drones.length, useExampleVehicles, useExampleDrones]);
  
  // 使用传入的数据或示例数据
  const currentDrones = drones.length > 0 ? drones : dynamicExampleDrones;
  const currentVehicles = vehicles.length > 0 ? vehicles : dynamicExampleVehicles;
  const currentWeather = filteredWeatherEvents.length > 0 ? filteredWeatherEvents : exampleWeather;
  
  return (
    <div style={{ height: '100%', width: '100%', background: '#f0f0f0', position: 'relative' }}>
      {/* 3D场景 */}
      <Canvas
        camera={{ position: [0, 50, 50], fov: 50 }}
        style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%' }}
        onCreated={({ camera }) => { cameraRef.current = camera; }}
      >
        <Suspense fallback={null}>
          <CameraController onCameraChange={handleCameraChange} />
          
          <Sky {...getSkyProps()} />
          <Environment preset={timeOfDay > 18 || timeOfDay < 6 ? "night" : "sunset"} />
          
          <ambientLight intensity={timeOfDay > 18 || timeOfDay < 6 ? 0.2 : 0.5} />
          <directionalLight
            position={[
              50 * Math.cos((timeOfDay / 24) * Math.PI * 2),
              50 * Math.sin((timeOfDay / 24) * Math.PI * 2),
              30
            ]}
            intensity={timeOfDay > 18 || timeOfDay < 6 ? 0.1 : 1}
          />
          
          <group scale={[sceneScale, sceneScale, sceneScale]}>
            <CityModel
              mapStyle={mapStyle}
              center={center}
              radius={radius}
              timeOfDay={timeOfDay}
            />
            
            {currentDrones.map(drone => (
              <Drone
                key={drone.id}
                position={drone.position}
                type={drone.type}
                id={drone.id}
                battery={drone.battery_level || drone.battery || 100}
                speed={drone.speed || 0}
                onClick={handleAgentClick}
              />
            ))}
            
            {currentVehicles.map(vehicle => (
              <Vehicle
                key={vehicle.id}
                position={vehicle.position}
                type={vehicle.type}
                id={vehicle.id}
                speed={vehicle.speed || 0}
                angle={vehicle.angle || 0} // 传递角度 prop
                onClick={handleAgentClick}
              />
            ))}
          </group>
          
          {currentWeather.map(event => (
            <WeatherEffect
              key={event.id}
              position={event.position}
              condition={event.condition}
              intensity={event.intensity || 1.0}
            />
          ))}
          
          <OrbitControls
            ref={controlsRef}
            enablePan={true} // 始终允许平移
            enableZoom={true} // 始终允许缩放
            enableRotate={true} // 始终允许旋转
            minDistance={5}
            maxDistance={500}
            mouseButtons={{
              LEFT: THREE.MOUSE.PAN, // 左键平移
              MIDDLE: THREE.MOUSE.DOLLY, // 中键缩放 (保持默认)
              RIGHT: THREE.MOUSE.ROTATE // 右键旋转
            }}
            screenSpacePanning={false} // 设置为 false 以实现 XY 平面平移
            onChange={() => {
              // 更新相机状态以便小地图同步
              if (cameraRef.current && controlsRef.current) {
                const position = cameraRef.current.position.clone();
                const target = controlsRef.current.target.clone();
                setCameraState(prevState => {
                  // 保留之前的zoom值，如果存在的话
                  return {
                    position,
                    target,
                    zoom: prevState?.zoom || (16 - Math.min(5, sceneScale * 3))
                  };
                });
              }
            }}
          />
        </Suspense>
        
        {/* 车辆移动管理器 - 使用稳定的条件渲染 */}
        {useExampleVehicles && (
          <VehicleMobilityManager
            initialVehicles={initialExampleVehicles}
            onVehiclesUpdate={handleVehiclesUpdate}
            enabled={true}
            syncWithBackend={true} // 启用与后端同步
            syncInterval={200} // 每2秒同步一次
            key="vehicle-mobility-manager" // 添加固定key，避免在严格模式下重新创建组件
          />
        )}
        
        {/* 无人机移动管理器 - 使用稳定的条件渲染 */}
        {useExampleDrones && (
          <DroneMobilityManager
            initialDrones={exampleDrones}
            onDronesUpdate={handleDronesUpdate}
            enabled={true}
            updateInterval={1000} // 每秒更新一次
            key="drone-mobility-manager" // 添加固定key，避免在严格模式下重新创建组件
          />
        )}
      </Canvas>
      
      {/* 小地图 */}
      <MiniMap
        drones={currentDrones}
        vehicles={currentVehicles}
        cameraPosition={cameraState?.position}
        cameraTarget={cameraState?.target}
        mapStyle={mapStyle}
        onMapClick={handleMapClick}
        onMapDrag={handleMapDrag}
        onMapCenterChange={handleMapCenterChange} // 添加中心点变化回调
        zoom={cameraState?.zoom || (16 - Math.min(5, sceneScale * 3))} // 动态计算缩放级别，与场景缩放相关
        baseCenter={center} // 传递基准中心点
      />
      
      {/* 地图信息 */}
      <div style={{
        position: 'absolute',
        bottom: 10,
        right: 10,
        background: 'rgba(255,255,255,0.7)',
        padding: '5px 10px',
        borderRadius: '4px',
        fontSize: '12px',
        zIndex: 10
      }}>
        <div>© OpenStreetMap 模拟视图</div>
        <div>地图样式: {mapStyle}</div>
        <div>当前时间: {timeOfDay}:00</div>
        {cameraState && (
          <div>
            <div>相机位置: ({cameraState.position.x.toFixed(1)}, {cameraState.position.y.toFixed(1)}, {cameraState.position.z.toFixed(1)})</div>
          </div>
        )}
      </div>
    </div>
  );
};

export default Simulation3DView;