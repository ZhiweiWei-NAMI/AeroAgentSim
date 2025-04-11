import React, { useState, useRef, useEffect, useCallback } from 'react';
import PropTypes from 'prop-types';
import * as THREE from 'three';
import CityModelVisual from './CityModelVisual';
import { fetchOSMData, osmDataCache, setupIdleCallback } from './OSMDataService';

// 兼容性处理：确保requestIdleCallback可用
setupIdleCallback();

// 主容器组件 - 负责数据获取、状态管理和LOD逻辑
const CityModel = React.forwardRef((props, ref) => {
  const { center, radius, mapStyle, timeOfDay, ...otherProps } = props;
  const [osmData, setOsmData] = useState({ buildings: [], roads: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [dataLoaded, setDataLoaded] = useState(false);
  const [cameraPosition, setCameraPosition] = useState(null);
  const [renderedBuildingCount, setRenderedBuildingCount] = useState(0);
  const processingRef = useRef(false);
  const buildingQueueRef = useRef([]);
  const idleCallbackIdRef = useRef(null);
  const visibleBuildingsRef = useRef({}); // 使用Ref来存储可见建筑物，避免不必要的重渲染

  // 使用字符串化的中心点和半径作为缓存键
  const cacheKey = JSON.stringify({ center, radius });

  // --- 数据加载逻辑 ---
  useEffect(() => {
    const loadOSMData = async () => {
      if (osmDataCache[cacheKey]) {
        console.log('从缓存加载OpenStreetMap数据');
        setOsmData(osmDataCache[cacheKey]);
        setLoading(false);
        setDataLoaded(true);
        buildingQueueRef.current = [...osmDataCache[cacheKey].buildings];
        processBuildingsIdle(); // 开始处理缓存中的建筑物
        return;
      }

      try {
        console.log('开始加载OpenStreetMap数据，中心点:', center, '半径:', radius);
        setLoading(true);
        setError(null); // 重置错误状态
        const data = await fetchOSMData(center, radius);
        console.log('成功获取OpenStreetMap数据');
        console.log('建筑物数量:', data.buildings.length, '道路数量:', data.roads.length);

        osmDataCache[cacheKey] = data; // 保存到缓存
        setOsmData(data);
        setLoading(false);
        setDataLoaded(true);
        buildingQueueRef.current = [...data.buildings];
        processBuildingsIdle(); // 开始处理新加载的建筑物
      } catch (err) {
        console.error('加载OpenStreetMap数据失败:', err);
        setError(err);
        setLoading(false);
      }
    };

    loadOSMData();

    // 清理函数
    return () => {
      if (idleCallbackIdRef.current !== null) {
        cancelIdleCallback(idleCallbackIdRef.current);
        idleCallbackIdRef.current = null;
      }
      // 可选：如果需要在组件卸载时停止处理
      processingRef.current = false;
    };
  }, [cacheKey, center, radius]); // 依赖项包括center和radius，以便在它们变化时重新加载

  // --- 相机位置更新 ---
  useEffect(() => {
    const handleCameraUpdate = (e) => {
      if (e.detail && e.detail.position) {
        setCameraPosition(e.detail.position);
        // 如果有待处理的建筑物且当前未在处理，则启动处理
        if (buildingQueueRef.current.length > 0 && !processingRef.current) {
          processBuildingsIdle();
        }
      }
    };
    window.addEventListener('camera-update', handleCameraUpdate);
    return () => window.removeEventListener('camera-update', handleCameraUpdate);
  }, []); // 空依赖数组，仅在挂载和卸载时运行

  // --- LOD 计算 ---
  const calculateLOD = useCallback((building, camPosVec) => {
    if (!camPosVec) return 'high'; // 默认高细节
    const buildingPos = new THREE.Vector3(building.position[0], 0, building.position[1]);
    const distance = buildingPos.distanceTo(camPosVec);
    if (distance < 100) return 'high';
    if (distance < 300) return 'medium';
    return 'low';
  }, []);

  // --- 建筑物空闲处理 ---
  const processBuildingsBatch = useCallback((deadline) => {
    if (buildingQueueRef.current.length === 0) {
      processingRef.current = false;
      return;
    }

    const startTime = performance.now();
    const batchSize = 20; // 增加每批处理量
    const newVisibleBuildings = { ...visibleBuildingsRef.current };
    let processedCount = 0;
    const camPosVec = cameraPosition ? new THREE.Vector3(cameraPosition.x, 0, cameraPosition.z) : null;

    while (buildingQueueRef.current.length > 0 && processedCount < batchSize &&
           (deadline.timeRemaining() > 1 || (performance.now() - startTime < 5))) { // 留出1ms余量
      const building = buildingQueueRef.current.shift();
      const lod = calculateLOD(building, camPosVec);

      // 根据LOD和随机性决定是否渲染，以减少远距离建筑物的密度
      let shouldRender = true;
      if (lod === 'low' && Math.random() > 0.3) { // 30%概率渲染低LOD
          shouldRender = false;
      } else if (lod === 'medium' && Math.random() > 0.6) { // 60%概率渲染中LOD
          shouldRender = false;
      }

      if (shouldRender) {
          newVisibleBuildings[building.id] = { ...building, lod };
      } else {
          // 如果不渲染，确保从可见列表中移除（如果之前存在）
          delete newVisibleBuildings[building.id];
      }
      processedCount++;
    }

    // 仅当 visibleBuildings 实际发生变化时才更新状态
    if (Object.keys(newVisibleBuildings).length !== Object.keys(visibleBuildingsRef.current).length ||
        Object.keys(newVisibleBuildings).some(id => !visibleBuildingsRef.current[id] || visibleBuildingsRef.current[id].lod !== newVisibleBuildings[id].lod)) {
      visibleBuildingsRef.current = newVisibleBuildings;
      // 强制更新以触发 CityModelVisual 的重渲染
      // 注意：这是一种hacky的方式，更好的方式可能是将visibleBuildings作为state管理，但可能导致性能问题
      // 这里我们依赖于CityModelVisual的React.memo来优化
      setRenderedBuildingCount(Object.keys(newVisibleBuildings).length); // 使用一个简单的state来触发更新
    }


    if (buildingQueueRef.current.length > 0) {
      idleCallbackIdRef.current = requestIdleCallback(processBuildingsBatch);
    } else {
      processingRef.current = false;
      console.log("所有建筑物处理完毕");
    }
  }, [cameraPosition, calculateLOD]); // 依赖项

  const processBuildingsIdle = useCallback(() => {
    if (processingRef.current || buildingQueueRef.current.length === 0) return;
    processingRef.current = true;
    console.log("开始处理建筑物队列:", buildingQueueRef.current.length);
    idleCallbackIdRef.current = requestIdleCallback(processBuildingsBatch);
  }, [processBuildingsBatch]);

  // --- 渲染 ---
  return (
    <CityModelVisual
      {...otherProps} // 传递其他未使用的props
      ref={ref} // 传递ref
      mapStyle={mapStyle}
      roads={osmData.roads}
      loading={loading}
      error={error}
      visibleBuildings={visibleBuildingsRef.current} // 传递ref中的数据
      timeOfDay={timeOfDay} // 传递时间参数，用于云朵效果
    />
  );
});

// PropTypes 定义
CityModel.propTypes = {
  mapStyle: PropTypes.oneOf(['standard', 'satellite', 'dark']),
  center: PropTypes.arrayOf(PropTypes.number).isRequired,
  radius: PropTypes.number.isRequired,
  timeOfDay: PropTypes.number, // 添加时间属性，用于云朵效果
  // 其他可能从父组件传递的props...
};

export default CityModel;