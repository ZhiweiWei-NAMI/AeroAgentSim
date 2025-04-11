import React, { useRef, useState, useMemo, useEffect } from 'react';
import { useFrame } from '@react-three/fiber';
import { Html } from '@react-three/drei';
import * as THREE from 'three';

// 车辆组件
const Vehicle = ({ position, type = 'car', id, speed = 0, angle = 0, onClick }) => {
  const vehicleRef = useRef();
  const [hovered, setHovered] = useState(false);
  const [clicked, setClicked] = useState(false);
  
  // 创建车轮的 refs 数组
  const wheelRefs = useMemo(() => Array(4).fill(0).map(() => React.createRef()), []);
  
  // 使用ref存储目标位置和角度，用于平滑插值
  const targetPositionRef = useRef(new THREE.Vector3(position[0], position[1], position[2]));
  const targetAngleRef = useRef(angle);
  const currentPositionRef = useRef(new THREE.Vector3(position[0], position[1], position[2]));
  const wheelRotationRef = useRef(0);
  
  // 当位置prop更新时，更新目标位置
  useEffect(() => {
    targetPositionRef.current.set(position[0], position[1], position[2]);
    targetAngleRef.current = angle;
  }, [position, angle]);
  
  // 车辆类型对应的尺寸和颜色
  const vehicleProps = useMemo(() => {
    switch(type) {
      case 'truck': 
        return { 
          size: [2, 1.5, 4], 
          color: '#1565C0', 
          wheelPositions: [
            [-1, 0.5, -1.5],
            [1, 0.5, -1.5],
            [-1, 0.5, 1.5],
            [1, 0.5, 1.5]
          ],
          cabinSize: [2, 1, 1.5],
          cabinPosition: [0, 1.5, -1.25],
          cargoSize: [1.8, 1.5, 2.5],
          cargoPosition: [0, 1.25, 0.5]
        };
      case 'van': 
        return { 
          size: [1.8, 1.8, 3], 
          color: '#2E7D32', 
          wheelPositions: [
            [-1, 0.5, -1],
            [1, 0.5, -1],
            [-1, 0.5, 1],
            [1, 0.5, 1]
          ],
          windowSize: [1.7, 0.6, 0.1],
          windowPosition: [0, 1.8, -0.5]
        };
      default: 
        return { 
          size: [1.5, 1, 3], 
          color: '#B0BEC5', 
          wheelPositions: [
            [-0.8, 0.5, -0.8],
            [0.8, 0.5, -0.8],
            [-0.8, 0.5, 0.8],
            [0.8, 0.5, 0.8]
          ],
          windowSize: [1.4, 0.5, 2],
          windowPosition: [0, 1.25, 0],
          roofHeight: 0.3
        };
    }
  }, [type]);
  
  // 使用 useFrame 实现平滑动画
  useFrame((state, delta) => {
    if (vehicleRef.current) {
      // 1. 位置插值逻辑
      const distance = currentPositionRef.current.distanceTo(targetPositionRef.current);
      const distanceThreshold = 0.0001;
      const lerpFactorPosition = Math.min(1, delta * 15);

      if (distance > distanceThreshold) {
        currentPositionRef.current.lerp(targetPositionRef.current, lerpFactorPosition);
        vehicleRef.current.position.copy(currentPositionRef.current);
      } else {
        vehicleRef.current.position.copy(targetPositionRef.current);
        if (currentPositionRef.current.distanceTo(targetPositionRef.current) > 1e-6) {
             currentPositionRef.current.copy(targetPositionRef.current);
        }
      }
      
      // 2. 平滑插值旋转
      const currentAngle = vehicleRef.current.rotation.y;
      // 注意：在Three.js中，车辆的默认朝向是沿着Z轴负方向
      // 但在我们的移动逻辑中，angle=0表示沿着X轴正方向
      // 所以需要加上Math.PI/2来调整方向
      const targetAngle = targetAngleRef.current - Math.PI/2;
      
      // 处理角度差，确保选择最短的旋转路径
      let angleDiff = targetAngle - currentAngle;
      // 标准化角度差到[-PI, PI]范围内
      while (angleDiff > Math.PI) angleDiff -= 2 * Math.PI;
      while (angleDiff < -Math.PI) angleDiff += 2 * Math.PI;
      
      // 使用绝对值来检查是否需要继续插值
      if (Math.abs(angleDiff) > 0.01) {
        // 使用角度差直接插值，而不是使用绝对角度
        vehicleRef.current.rotation.y += angleDiff * delta * 5;
      } else {
        vehicleRef.current.rotation.y = targetAngle;
      }
      
      // 3. 旋转车轮
      if (speed > 0) {
        const wheelRadius = 0.5;
        const speedFactor = 5.0;
        const rotationIncrement = (delta * speed * speedFactor) / wheelRadius;
        
        wheelRotationRef.current += rotationIncrement;
        
        wheelRefs.forEach(wheelRef => {
          if (wheelRef.current) {
            wheelRef.current.rotation.x = wheelRotationRef.current;
          }
        });
        
        // 添加轻微的上下运动，模拟悬挂系统
        if (vehicleRef.current) {
          vehicleRef.current.position.y = position[1] + Math.sin(state.clock.elapsedTime * 10) * 0.01 * (speed/10);
        }
      }
    }
  });
  
  // 渲染车辆
  // 调整车辆模型的初始朝向，使其与移动方向一致
  // 在Three.js中，默认模型朝向Z轴负方向，但我们的移动逻辑中angle=0表示X轴正方向
  return (
    <group
      rotation={[0, Math.PI/2, 0]} // 初始旋转90度，使车辆默认朝向X轴正方向
      ref={vehicleRef}
      onPointerOver={() => setHovered(true)}
      onPointerOut={() => setHovered(false)}
      onClick={(e) => {
        e.stopPropagation();
        setClicked(true);
        if (onClick) {
          onClick([
            currentPositionRef.current.x,
            currentPositionRef.current.y,
            currentPositionRef.current.z
          ]);
        }
        setTimeout(() => setClicked(false), 300);
      }}
      onPointerEnter={() => document.body.style.cursor = 'pointer'}
      onPointerLeave={() => document.body.style.cursor = 'auto'}
    >
      {/* 车辆主体 */}
      {type === 'truck' ? (
        // 卡车模型
        <>
          {/* 底盘 */}
          <mesh position={[0, vehicleProps.size[1] / 2, 0]}>
            <boxGeometry args={vehicleProps.size} />
            <meshStandardMaterial
              color={vehicleProps.color}
              metalness={0.2}
              roughness={0.7}
              emissive={clicked ? "#ffffff" : "#000000"}
              emissiveIntensity={clicked ? 0.5 : 0}
            />
          </mesh>
          
          {/* 驾驶室 */}
          <mesh position={vehicleProps.cabinPosition}>
            <boxGeometry args={vehicleProps.cabinSize} />
            <meshStandardMaterial
              color="#263238"
              metalness={0.3}
              roughness={0.6}
            />
          </mesh>
          
          {/* 货箱 */}
          <mesh position={vehicleProps.cargoPosition}>
            <boxGeometry args={vehicleProps.cargoSize} />
            <meshStandardMaterial
              color="#455A64"
              metalness={0.1}
              roughness={0.8}
            />
          </mesh>
        </>
      ) : type === 'van' ? (
        // 面包车模型
        <>
          {/* 车身 */}
          <mesh position={[0, vehicleProps.size[1] / 2, 0]}>
            <boxGeometry args={vehicleProps.size} />
            <meshStandardMaterial
              color={vehicleProps.color}
              metalness={0.2}
              roughness={0.7}
              emissive={clicked ? "#ffffff" : "#000000"}
              emissiveIntensity={clicked ? 0.5 : 0}
            />
          </mesh>
          
          {/* 窗户 */}
          <mesh position={vehicleProps.windowPosition}>
            <boxGeometry args={vehicleProps.windowSize} />
            <meshStandardMaterial
              color="#90CAF9"
              metalness={0.8}
              roughness={0.2}
              transparent={true}
              opacity={0.7}
            />
          </mesh>
        </>
      ) : (
        // 普通小汽车模型
        <>
          {/* 车身 */}
          <mesh position={[0, vehicleProps.size[1] / 2, 0]}>
            <boxGeometry args={vehicleProps.size} />
            <meshStandardMaterial
              color={vehicleProps.color}
              metalness={0.4}
              roughness={0.6}
              emissive={clicked ? "#ffffff" : "#000000"}
              emissiveIntensity={clicked ? 0.5 : 0}
            />
          </mesh>
          
          {/* 车顶 */}
          <mesh position={[0, vehicleProps.size[1] + vehicleProps.roofHeight/2, 0]}>
            <boxGeometry args={[vehicleProps.size[0] * 0.8, vehicleProps.roofHeight, vehicleProps.size[2] * 0.6]} />
            <meshStandardMaterial
              color="#37474F"
              metalness={0.3}
              roughness={0.7}
            />
          </mesh>
          
          {/* 窗户 */}
          <mesh position={vehicleProps.windowPosition}>
            <boxGeometry args={vehicleProps.windowSize} />
            <meshStandardMaterial
              color="#90CAF9"
              metalness={0.8}
              roughness={0.2}
              transparent={true}
              opacity={0.7}
            />
          </mesh>
        </>
      )}
      
      {/* 车轮 - 使用ref来控制旋转 */}
      {vehicleProps.wheelPositions.map((pos, i) => (
        <mesh key={i} ref={wheelRefs[i]} position={pos} rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[0.5, 0.5, 0.3, 16]} />
          <meshStandardMaterial color="black" />
          
          {/* 轮毂 */}
          <mesh position={[0, 0, 0]}>
            <cylinderGeometry args={[0.25, 0.25, 0.31, 8]} />
            <meshStandardMaterial color="#CFD8DC" metalness={0.8} roughness={0.2} />
          </mesh>
        </mesh>
      ))}
      
      {/* 车灯 */}
      {[
        [-vehicleProps.size[0]/2 + 0.1, vehicleProps.size[1]/2, -vehicleProps.size[2]/2 - 0.1],
        [vehicleProps.size[0]/2 - 0.1, vehicleProps.size[1]/2, -vehicleProps.size[2]/2 - 0.1]
      ].map((pos, i) => (
        <mesh key={`headlight-${i}`} position={pos}>
          <sphereGeometry args={[0.2, 8, 8, 0, Math.PI * 2, 0, Math.PI / 2]} />
          <meshStandardMaterial 
            color="#FFECB3" 
            emissive="#FFECB3"
            emissiveIntensity={speed > 0 ? 0.8 : 0}
          />
        </mesh>
      ))}
      
      {/* 信息标签 */}
      <Html distanceFactor={15} position={[0, vehicleProps.size[1] + 1, 0]} center>
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
          <div>{type.charAt(0).toUpperCase() + type.slice(1)} {id || ''}</div>
          {hovered && typeof speed === 'number' && !isNaN(speed) && (
            <div>速度: {speed.toFixed(1)} km/h</div>
          )}
        </div>
      </Html>
    </group>
  );
};

export default Vehicle;