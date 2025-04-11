import React, { useRef, useState, useMemo } from 'react';
import { useFrame } from '@react-three/fiber';
import { Html } from '@react-three/drei';
import * as THREE from 'three';

// 无人机组件
const Drone = ({ position, type = 'default', id, battery = 100, speed = 0, onClick }) => {
  const droneRef = useRef();
  const [hovered, setHovered] = useState(false);
  const [clicked, setClicked] = useState(false);
  
  // 无人机类型对应的属性
  const droneProps = useMemo(() => {
    switch(type) {
      case 'inspector': 
        return { 
          color: '#F44336', // 红色
          size: 1.2,
          rotorCount: 4,
          hasCargo: false,
          hasCamera: true
        };
      case 'delivery': 
        return { 
          color: '#2196F3', // 蓝色
          size: 1.5,
          rotorCount: 6,
          hasCargo: true,
          hasCamera: true
        };
      case 'rescue': 
        return { 
          color: '#FF9800', // 橙色
          size: 1.3,
          rotorCount: 4,
          hasCargo: true,
          hasCamera: true
        };
      default: 
        return { 
          color: '#009688', // 青色
          size: 1.0,
          rotorCount: 4,
          hasCargo: false,
          hasCamera: false
        };
    }
  }, [type]);
  
  // 创建旋翼refs
  const rotorRefs = useMemo(() => {
    return Array(droneProps.rotorCount).fill(0).map(() => React.createRef());
  }, [droneProps.rotorCount]);
  
  // 无人机动画
  useFrame((state, delta) => {
    if (droneRef.current) {
      // 添加轻微的悬停动画
      droneRef.current.position.y = position[1] + Math.sin(state.clock.elapsedTime * 2) * 0.05;
      
      // 旋转旋翼
      rotorRefs.forEach((rotorRef, index) => {
        if (rotorRef.current) {
          // 奇偶旋翼反向旋转
          const direction = index % 2 === 0 ? 1 : -1;
          rotorRef.current.rotation.y += direction * delta * (10 + speed * 0.5);
        }
      });
    }
  });
  
  // 计算旋翼位置
  const rotorPositions = useMemo(() => {
    const positions = [];
    const radius = droneProps.size * 0.7;
    
    for (let i = 0; i < droneProps.rotorCount; i++) {
      const angle = (i / droneProps.rotorCount) * Math.PI * 2;
      const x = Math.cos(angle) * radius;
      const z = Math.sin(angle) * radius;
      positions.push([x, 0.2, z]);
    }
    
    return positions;
  }, [droneProps.rotorCount, droneProps.size]);
  
  // 计算电池颜色
  const batteryColor = useMemo(() => {
    if (battery > 70) return '#4CAF50'; // 绿色
    if (battery > 30) return '#FFC107'; // 黄色
    return '#F44336'; // 红色
  }, [battery]);
  
  return (
    <group
      ref={droneRef}
      position={position}
      onPointerOver={() => setHovered(true)}
      onPointerOut={() => setHovered(false)}
      onClick={(e) => {
        e.stopPropagation();
        setClicked(true);
        if (onClick) {
          onClick(position);
        }
        setTimeout(() => setClicked(false), 300);
      }}
      onPointerEnter={() => document.body.style.cursor = 'pointer'}
      onPointerLeave={() => document.body.style.cursor = 'auto'}
    >
      {/* 无人机主体 */}
      <mesh>
        <boxGeometry args={[droneProps.size, 0.2, droneProps.size]} />
        <meshStandardMaterial
          color={droneProps.color}
          metalness={0.3}
          roughness={0.7}
          emissive={clicked ? "#ffffff" : "#000000"}
          emissiveIntensity={clicked ? 0.5 : 0}
        />
      </mesh>
      
      {/* 无人机中心舱 */}
      <mesh position={[0, 0.2, 0]}>
        <cylinderGeometry args={[droneProps.size * 0.3, droneProps.size * 0.3, 0.3, 8]} />
        <meshStandardMaterial
          color="#37474F"
          metalness={0.4}
          roughness={0.6}
        />
      </mesh>
      
      {/* 相机（如果有） */}
      {droneProps.hasCamera && (
        <mesh position={[0, -0.1, droneProps.size * 0.4]}>
          <sphereGeometry args={[0.1, 16, 16]} />
          <meshStandardMaterial
            color="#212121"
            metalness={0.8}
            roughness={0.2}
          />
        </mesh>
      )}
      
      {/* 货舱（如果有） */}
      {droneProps.hasCargo && (
        <mesh position={[0, -0.2, 0]}>
          <boxGeometry args={[droneProps.size * 0.5, 0.2, droneProps.size * 0.5]} />
          <meshStandardMaterial
            color="#546E7A"
            metalness={0.2}
            roughness={0.8}
          />
        </mesh>
      )}
      
      {/* 旋翼 */}
      {rotorPositions.map((pos, i) => (
        <group key={i} position={pos}>
          {/* 旋翼支架 */}
          <mesh>
            <cylinderGeometry args={[0.05, 0.05, 0.2, 8]} />
            <meshStandardMaterial color="#616161" />
          </mesh>
          
          {/* 旋翼 */}
          <mesh ref={rotorRefs[i]} position={[0, 0.1, 0]}>
            <cylinderGeometry args={[0.5, 0.5, 0.02, 16]} />
            <meshStandardMaterial color="#9E9E9E" opacity={0.7} transparent={true} />
          </mesh>
        </group>
      ))}
      
      {/* 状态灯 */}
      <mesh position={[0, 0.3, 0]}>
        <sphereGeometry args={[0.1, 16, 16]} />
        <meshStandardMaterial
          color={speed > 0 ? "#4CAF50" : "#FFC107"}
          emissive={speed > 0 ? "#4CAF50" : "#FFC107"}
          emissiveIntensity={0.5}
        />
      </mesh>
      
      {/* 电池指示灯 */}
      <mesh position={[droneProps.size * 0.3, 0.3, 0]}>
        <boxGeometry args={[0.2, 0.1, 0.1]} />
        <meshStandardMaterial
          color={batteryColor}
          emissive={batteryColor}
          emissiveIntensity={0.5}
        />
      </mesh>
      
      {/* 信息标签 */}
      <Html distanceFactor={15} position={[0, 1.5, 0]} center>
        <div style={{
          color: 'white',
          backgroundColor: hovered ? 'rgba(0,0,0,0.8)' : 'rgba(0,0,0,0.5)',
          padding: '5px 10px',
          borderRadius: '4px',
          fontSize: hovered ? '14px' : '12px',
          whiteSpace: 'nowrap',
          transition: 'all 0.3s',
          transform: hovered ? 'scale(1.1)' : 'scale(1)',
          pointerEvents: 'none'
        }}>
          <div>ID: {id || 'Unknown'}</div>
          {hovered && (
            <>
              <div>类型: {type}</div>
              <div>电量: {battery.toFixed(1)}%</div>
              <div>速度: {speed.toFixed(1)} m/s</div>
            </>
          )}
        </div>
      </Html>
    </group>
  );
};

export default Drone;