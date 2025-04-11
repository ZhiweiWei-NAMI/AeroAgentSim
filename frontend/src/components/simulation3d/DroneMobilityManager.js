import React, { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import AgentController from './AgentController';

// 无人机移动管理器组件
const DroneMobilityManager = ({ 
  initialDrones = [], 
  onDronesUpdate = () => {},
  enabled = true,
  boundary = 5000,
  updateInterval = 1000 // 默认每秒更新一次
}) => {
  // 使用 ref 和 state 结合管理无人机数据
  // ref用于避免不必要的重新渲染，state用于触发必要的UI更新
  const dronesRef = useRef(initialDrones);
  const [drones, setDrones] = useState(initialDrones);
  
  // 初始化dronesRef
  useEffect(() => {
    dronesRef.current = initialDrones;
    setDrones(initialDrones);
  }, [initialDrones]);
  
  // 处理无人机位置更新
  const handlePositionUpdate = useCallback((updatedDrones) => {
    // 更新ref和state
    dronesRef.current = updatedDrones;
    setDrones(updatedDrones);
    onDronesUpdate(updatedDrones);
  }, [onDronesUpdate]);
  
  // 使用useMemo缓存无人机数据
  const currentDrones = useMemo(() => {
    return drones;
  }, [drones]);
  
  // 使用通用代理控制器来管理无人机移动
  return (
    <AgentController
      agents={currentDrones}
      type="drone"
      onPositionUpdate={handlePositionUpdate}
      enabled={enabled}
      defaultSpeed={5} // 默认无人机速度
      updateInterval={updateInterval}
      boundary={boundary}
      key="drone-controller" // 添加固定key，避免在严格模式下重新创建组件
    />
  );
};

export default DroneMobilityManager;