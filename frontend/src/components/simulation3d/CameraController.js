import React, { useRef, useEffect } from 'react';
import PropTypes from 'prop-types';
import * as THREE from 'three';
import { useThree, useFrame } from '@react-three/fiber';

/**
 * 相机控制器组件
 * 
 * 该组件负责监听相机变化并通知父组件
 * 它不渲染任何可见元素，只处理相机逻辑
 */
const CameraController = ({ 
  onCameraChange, 
  lockTarget = null,
  cameraLocked = false,
  lockHeight = 100,
  damping = 0.5  // 相机移动的阻尼系数，使移动更平滑
}) => {
  const { camera, scene } = useThree();
  const prevPositionRef = useRef(new THREE.Vector3());
  const prevTargetRef = useRef(new THREE.Vector3());
  const frameCountRef = useRef(0);

  // 存储当前锁定目标的引用
  const lockTargetRef = useRef(lockTarget);
  const cameraLockedRef = useRef(cameraLocked);

  // 当锁定状态或目标变化时更新引用
  useEffect(() => {
    cameraLockedRef.current = cameraLocked;
    lockTargetRef.current = lockTarget;
  }, [cameraLocked, lockTarget]);

  // 使用useFrame钩子在每一帧检查相机变化
  useFrame(() => {
    // 增加帧计数器
    frameCountRef.current += 1;
    
    // 每5帧检查一次相机变化，以减少不必要的更新
    if (frameCountRef.current % 5 !== 0) return;
    
    // 处理相机锁定逻辑
    if (cameraLockedRef.current && lockTargetRef.current) {
      const targetPosition = Array.isArray(lockTargetRef.current) 
        ? new THREE.Vector3(lockTargetRef.current[0], lockTargetRef.current[1], lockTargetRef.current[2])
        : lockTargetRef.current;
      
      // 计算目标位置上方的相机位置
      const cameraPosition = new THREE.Vector3(
        targetPosition.x,
        targetPosition.y + lockHeight,
        targetPosition.z
      );
      
      // 使用阻尼平滑移动相机
      camera.position.lerp(cameraPosition, damping);
      
      // 发送相机更新事件
      if (onCameraChange) {
        onCameraChange({
          position: camera.position.clone(),
          target: targetPosition.clone()
        });
      }
      
      // 发送自定义事件以便其他组件可以响应
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('camera-update', {
          detail: {
            position: camera.position.clone(),
            target: targetPosition.clone()
          }
        }));
      }
      
      return;
    }
    
    // 检查相机位置是否发生变化
    const currentPosition = camera.position.clone();
    const hasPositionChanged = !currentPosition.equals(prevPositionRef.current);
    
    // 如果位置发生变化，通知父组件
    if (hasPositionChanged) {
      prevPositionRef.current.copy(currentPosition);
      
      // 获取相机的目标点（在Three.js中通常是控制器的target）
      // 这里我们假设场景中心是目标点
      const target = new THREE.Vector3(0, 0, 0);
      
      // 尝试从场景中找到OrbitControls
      scene.traverse(object => {
        if (object.isOrbitControls) {
          target.copy(object.target);
        }
      });
      
      // 只有当位置或目标发生变化时才触发回调
      if (hasPositionChanged || !target.equals(prevTargetRef.current)) {
        prevTargetRef.current.copy(target);
        
        if (onCameraChange) {
          onCameraChange({
            position: currentPosition,
            target: target
          });
        }
        
        // 发送自定义事件以便其他组件可以响应
        if (typeof window !== 'undefined') {
          window.dispatchEvent(new CustomEvent('camera-update', {
            detail: {
              position: currentPosition,
              target: target
            }
          }));
        }
      }
    }
  });

  // 组件挂载时初始化
  useEffect(() => {
    // 初始化前一个位置为当前相机位置
    prevPositionRef.current.copy(camera.position);
    
    // 触发初始相机状态更新
    if (onCameraChange) {
      onCameraChange({
        position: camera.position.clone(),
        target: new THREE.Vector3(0, 0, 0) // 默认目标点
      });
    }
    
    // 清理函数
    return () => {
      // 组件卸载时的清理逻辑
    };
  }, [camera, onCameraChange]);

  // 这个组件不渲染任何可见内容
  return null;
};

CameraController.propTypes = {
  onCameraChange: PropTypes.func,
  lockTarget: PropTypes.oneOfType([
    PropTypes.arrayOf(PropTypes.number),
    PropTypes.object
  ]),
  cameraLocked: PropTypes.bool,
  lockHeight: PropTypes.number
};

export default CameraController;