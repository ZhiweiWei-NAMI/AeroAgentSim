import React, { useRef, useCallback, useEffect, useMemo } from 'react';
import AgentController from './AgentController';
import { vehicleApi } from '../../services/api';
import * as THREE from 'three';

// 车辆移动管理器组件
const VehicleMobilityManager = ({
  initialVehicles = [],
  onVehiclesUpdate = () => {},
  enabled = true,
  boundary = 5000,
  syncWithBackend = false, // 是否与后端同步
  syncInterval = 2000 // 同步间隔，默认2秒
}) => {
  // 使用 ref 管理车辆数据，避免不必要的重新渲染
  const vehiclesRef = useRef(initialVehicles);
  // 使用 ref 存储上次同步的时间戳
  const lastSyncTimestampRef = useRef(Date.now());
  // 使用 ref 存储是否正在同步
  const isSyncingRef = useRef(false);
  // 使用 ref 存储同步定时器ID
  const syncTimerRef = useRef(null);
  // 使用 ref 存储上一次仿真时间
  const previousSimTimeRef = useRef(0);
  
  // 初始化vehiclesRef
  useEffect(() => {
    vehiclesRef.current = initialVehicles;
  }, [initialVehicles]);
  
  // 从后端获取车辆数据
  const fetchVehiclesFromBackend = useCallback(async () => {
    if (isSyncingRef.current) return; // 如果正在同步，则跳过
    
    try {
      isSyncingRef.current = true;
      const backendVehicles = await vehicleApi.getAllVehicles();
      
      if (backendVehicles && backendVehicles.length > 0) {
        // 获取当前时间戳
        const currentTimestamp = Date.now();
        
        // 获取最新的仿真时间
        const latestSimTime = Math.max(...backendVehicles.map(v => v.sim_time || 0));
        
        // 检查是否需要更新 - 只有当新的仿真时间大于当前仿真时间时才更新
        if (latestSimTime <= previousSimTimeRef.current && previousSimTimeRef.current > 0) {
          console.log(`跳过更新：新仿真时间(${latestSimTime})未超过当前仿真时间(${previousSimTimeRef.current})`);
          isSyncingRef.current = false;
          return;
        }
        
        // 更新时间戳 - 这个时间戳将触发AgentController中的动画更新
        lastSyncTimestampRef.current = currentTimestamp;
        
        // 将后端数据转换为前端需要的格式
        const formattedVehicles = backendVehicles.map(vehicle => {
          // 确保vehicle.position是有效的三维数组
          let position = vehicle.position;
          if (!position || !Array.isArray(position) || position.length !== 3) {
            position = [0, 0, 0];
          }
          
          // 获取仿真时间
          const simTime = vehicle.sim_time || 0;
          
          // 查找现有车辆
          const existingVehicle = vehiclesRef.current?.find(v => v.id === vehicle.id);
          if (existingVehicle) {
            console.log(`更新车辆 ${vehicle.id} 的位置`);
          } else {
            console.log(`添加新车辆 ${vehicle.id}`);
          }
          // 重要：保留现有的posRef引用，但不更新其值
          // 这样AgentController可以从当前显示位置平滑过渡到新位置
          const posRef = existingVehicle?.posRef || new THREE.Vector3(position[0], position[1], position[2]);
          
          return {
            id: vehicle.id,
            // position是从后端获取的目标位置
            position: position,
            // posRef是当前显示位置的引用，AgentController将使用它进行动画
            posRef: posRef,
            type: vehicle.type || 'car',
            speed: vehicle.speed || 0,
            angle: vehicle.angle || 0,
            timestamp: currentTimestamp,
            simTime: simTime
          };
        });
        
        // 更新车辆数据
        vehiclesRef.current = formattedVehicles;
        onVehiclesUpdate(formattedVehicles);
        
        // 更新上一次仿真时间
        previousSimTimeRef.current = latestSimTime;
        
        console.log(`已从后端同步${formattedVehicles.length}辆车的数据，仿真时间：${latestSimTime}`);
      }
    } catch (error) {
      console.error('从后端获取车辆数据失败:', error);
    } finally {
      isSyncingRef.current = false;
    }
  }, [onVehiclesUpdate]);
  
  // 设置定期同步
  useEffect(() => {
    if (syncWithBackend && enabled) {
      // 初始立即同步一次
      fetchVehiclesFromBackend();
      
      // 设置定期同步
      syncTimerRef.current = setInterval(() => {
        fetchVehiclesFromBackend();
      }, syncInterval);
      
      return () => {
        if (syncTimerRef.current) {
          clearInterval(syncTimerRef.current);
          syncTimerRef.current = null;
        }
      };
    }
  }, [syncWithBackend, enabled, syncInterval, fetchVehiclesFromBackend]);
  
  // 处理车辆位置更新
  const handlePositionUpdate = useCallback((updatedVehicles) => {
    // 更新ref而不是state，避免触发重新渲染
    vehiclesRef.current = updatedVehicles;
    // 仅通知父组件，不触发自身重新渲染
    onVehiclesUpdate(updatedVehicles);
  }, [onVehiclesUpdate]);
  
  // 使用useMemo缓存车辆数据，只有在vehiclesRef.current变化时才重新创建
  const currentVehicles = useMemo(() => {
    return vehiclesRef.current;
  }, [vehiclesRef.current]);
  
  // 使用通用代理控制器来管理车辆移动
  return (
    <AgentController
      agents={currentVehicles}
      type="vehicle"
      onPositionUpdate={handlePositionUpdate}
      enabled={enabled}
      boundary={boundary}
      key="vehicle-controller" // 添加固定key，避免在严格模式下重新创建组件
      syncWithBackend={syncWithBackend} // 传递是否与后端同步的标志
      lastSyncTimestamp={lastSyncTimestampRef.current} // 传递上次同步时间戳
      syncInterval={syncInterval} // 传递同步间隔，用于计算插值动画进度
    />
  );
};

export default VehicleMobilityManager;