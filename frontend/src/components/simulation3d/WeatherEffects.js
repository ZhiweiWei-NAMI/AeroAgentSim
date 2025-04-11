import React, { useRef, useState, useEffect } from 'react';
import { useFrame } from '@react-three/fiber';
import { Html } from '@react-three/drei';
import * as THREE from 'three';

// 气象效果组件
const WeatherEffect = ({ position, condition = 'sunny', intensity = 1.0 }) => {
  const weatherRef = useRef();
  
  // 不同气象条件的渲染
  const renderWeatherEffect = () => {
    switch(condition) {
      case 'rain':
        return <RainEffect position={position} intensity={intensity} />;
      case 'wind':
        return <WindEffect position={position} intensity={intensity} />;
      case 'fog':
        return <FogEffect position={position} intensity={intensity} />;
      case 'storm':
        return <StormEffect position={position} intensity={intensity} />;
      default:
        return <SunnyEffect position={position} />;
    }
  };
  
  return (
    <group ref={weatherRef} position={position}>
      {renderWeatherEffect()}
      
      {/* 气象图标 */}
      <Html position={[0, 0, 0]} center>
        <div style={{
          fontSize: '30px',
          background: 'rgba(255, 255, 255, 0.7)',
          padding: '5px',
          borderRadius: '50%',
          boxShadow: '0 0 10px rgba(0,0,0,0.2)'
        }}>
          {condition === 'rain' && '🌧️'}
          {condition === 'wind' && '💨'}
          {condition === 'fog' && '🌫️'}
          {condition === 'storm' && '⛈️'}
          {condition === 'sunny' && '☀️'}
        </div>
      </Html>
    </group>
  );
};

// 雨效果
const RainEffect = ({ position, intensity = 1.0 }) => {
  const rainRef = useRef();
  const rainCount = Math.floor(500 * intensity);
  const rainPositions = Array(rainCount).fill().map(() => [
    (Math.random() - 0.5) * 20,
    Math.random() * 20,
    (Math.random() - 0.5) * 20
  ]);
  
  useFrame((state, delta) => {
    if (rainRef.current) {
      // 雨滴下落动画
      const positions = rainRef.current.geometry.attributes.position.array;
      for (let i = 0; i < positions.length; i += 3) {
        positions[i + 1] -= delta * 10 * (Math.random() * 0.5 + 0.5);
        if (positions[i + 1] < 0) {
          positions[i + 1] = 20;
        }
      }
      rainRef.current.geometry.attributes.position.needsUpdate = true;
    }
  });
  
  return (
    <points ref={rainRef}>
      <bufferGeometry>
        <bufferAttribute
          attach="attributes-position"
          count={rainCount}
          array={new Float32Array(rainPositions.flat())}
          itemSize={3}
        />
      </bufferGeometry>
      <pointsMaterial
        size={0.1}
        color="#88ccff"
        transparent
        opacity={0.6}
      />
    </points>
  );
};

// 风效果
const WindEffect = ({ position, intensity = 1.0 }) => {
  const windRef = useRef();
  const windCount = Math.floor(100 * intensity);
  const windPositions = Array(windCount).fill().map(() => [
    (Math.random() - 0.5) * 20,
    (Math.random() - 0.5) * 10 + 5,
    (Math.random() - 0.5) * 20
  ]);
  
  useFrame((state, delta) => {
    if (windRef.current) {
      // 风的动画
      const positions = windRef.current.geometry.attributes.position.array;
      for (let i = 0; i < positions.length; i += 3) {
        positions[i] -= delta * 5 * intensity;
        if (positions[i] < -10) {
          positions[i] = 10;
        }
      }
      windRef.current.geometry.attributes.position.needsUpdate = true;
    }
  });
  
  return (
    <points ref={windRef}>
      <bufferGeometry>
        <bufferAttribute
          attach="attributes-position"
          count={windCount}
          array={new Float32Array(windPositions.flat())}
          itemSize={3}
        />
      </bufferGeometry>
      <pointsMaterial
        size={0.3}
        color="#ffffff"
        transparent
        opacity={0.4}
      />
    </points>
  );
};

// 雾效果
const FogEffect = ({ position, intensity = 1.0 }) => {
  return (
    <mesh position={position}>
      <sphereGeometry args={[10 * intensity, 16, 16]} />
      <meshStandardMaterial
        color="#aaaaaa"
        transparent
        opacity={0.2 * intensity}
      />
    </mesh>
  );
};

// 暴风雨效果
const StormEffect = ({ position, intensity = 1.0 }) => {
  const stormRef = useRef();
  const [lightningFlash, setLightningFlash] = useState(false);
  
  useEffect(() => {
    // 随机闪电效果
    const interval = setInterval(() => {
      if (Math.random() < 0.2 * intensity) {
        setLightningFlash(true);
        setTimeout(() => setLightningFlash(false), 100);
      }
    }, 2000);
    
    return () => clearInterval(interval);
  }, [intensity]);
  
  return (
    <group ref={stormRef}>
      <RainEffect position={[0, 0, 0]} intensity={intensity * 1.5} />
      {lightningFlash && (
        <pointLight
          position={[0, 5, 0]}
          intensity={5}
          color="#aaccff"
          distance={50}
        />
      )}
      <mesh>
        <sphereGeometry args={[5 * intensity, 16, 16]} />
        <meshStandardMaterial
          color="#334455"
          transparent
          opacity={0.4 * intensity}
        />
      </mesh>
    </group>
  );
};

// 晴天效果
const SunnyEffect = ({ position }) => {
  return (
    <pointLight
      position={[0, 5, 0]}
      intensity={1}
      color="#ffdd88"
      distance={30}
    />
  );
};

export { WeatherEffect, RainEffect, WindEffect, FogEffect, StormEffect, SunnyEffect };
export default WeatherEffect;