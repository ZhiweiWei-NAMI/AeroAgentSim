import React, { useMemo, useRef } from 'react';
import * as THREE from 'three';
import TextureGenerator from './TextureGenerator';

// 创建纹理缓存系统
const textureCache = {
  brick: {},
  window: {},
  road: {},
  commercial: {},
  highway: {}
};

// 获取缓存纹理的函数
const getCachedTexture = (type, key, generator) => {
  if (!textureCache[type][key]) {
    textureCache[type][key] = generator();
  }
  return textureCache[type][key];
};

// 建筑物组件 - 使用动态生成的纹理
export const EnhancedBuilding = ({ position, width, height, depth, type = 'residential', lod = 'high' }) => {
  // 使用useRef来存储随机值，避免每次渲染时重新生成
  const randomRef = useRef({
    hue: Math.random() * 30,
    hasExtraFeatures: Math.random() > 0.5,
    heightFactor: 0.9 + Math.random() * 0.2,
    extraFeatures: {
      hasWaterTank: Math.random() > 0.5,
      hasAntenna: Math.random() > 0.3
    }
  });

  // 生成建筑物的主要纹理和细节纹理 - 使用缓存系统
  const [mainTexture, detailTexture, roofTexture] = useMemo(() => {
    if (lod === 'low') {
      // 低细节模式使用简单纹理
      return [
        getCachedTexture('brick', 'low_main', () =>
          TextureGenerator.generateBrickTexture('#a0a0a0', '#808080')),
        null,
        getCachedTexture('brick', 'low_roof', () =>
          TextureGenerator.generateBrickTexture('#505050', '#404040'))
      ];
    }
    
    let main, detail, roof;
    
    switch(type) {
      case 'commercial':
        main = getCachedTexture('commercial', 'main', () =>
          TextureGenerator.generateCommercialBuildingTexture('#607D8B', '#B3E5FC'));
        detail = getCachedTexture('window', 'commercial', () =>
          TextureGenerator.generateWindowTexture('#E1F5FE', '#455A64'));
        roof = getCachedTexture('brick', 'commercial_roof', () =>
          TextureGenerator.generateBrickTexture('#455A64', '#37474F'));
        break;
      case 'industrial':
        main = getCachedTexture('brick', 'industrial_main', () =>
          TextureGenerator.generateBrickTexture('#78909C', '#546E7A'));
        detail = getCachedTexture('brick', 'industrial_detail', () =>
          TextureGenerator.generateBrickTexture('#546E7A', '#455A64'));
        roof = getCachedTexture('brick', 'industrial_roof', () =>
          TextureGenerator.generateBrickTexture('#37474F', '#263238'));
        break;
      case 'skyscraper':
        main = getCachedTexture('commercial', 'skyscraper_main', () =>
          TextureGenerator.generateCommercialBuildingTexture('#455A64', '#E1F5FE'));
        detail = getCachedTexture('window', 'skyscraper', () =>
          TextureGenerator.generateWindowTexture('#B3E5FC', '#263238'));
        roof = getCachedTexture('brick', 'skyscraper_roof', () =>
          TextureGenerator.generateBrickTexture('#263238', '#37474F'));
        break;
      default: // residential
        const { hue } = randomRef.current;
        const baseColor = `hsl(${hue}, 60%, 40%)`;
        const brickColor = `hsl(${hue}, 50%, 30%)`;
        const cacheKey = `residential_${Math.floor(hue)}`;
        
        main = getCachedTexture('brick', cacheKey + '_main', () =>
          TextureGenerator.generateBrickTexture(baseColor, brickColor));
        detail = getCachedTexture('window', 'residential', () =>
          TextureGenerator.generateWindowTexture('#B3E5FC', '#455A64'));
        roof = getCachedTexture('brick', 'residential_roof', () =>
          TextureGenerator.generateBrickTexture('#5D4037', '#4E342E'));
        break;
    }
    
    // 复制纹理并设置重复 - 避免修改缓存的纹理
    const mainCopy = main.clone();
    mainCopy.wrapS = mainCopy.wrapT = THREE.RepeatWrapping;
    mainCopy.repeat.set(width/10, height/10);
    
    let detailCopy = null;
    if (detail) {
      detailCopy = detail.clone();
      detailCopy.wrapS = detailCopy.wrapT = THREE.RepeatWrapping;
      detailCopy.repeat.set(width/10, height/10);
    }
    
    const roofCopy = roof.clone();
    roofCopy.wrapS = roofCopy.wrapT = THREE.RepeatWrapping;
    roofCopy.repeat.set(width/10, height/10);
    
    return [mainCopy, detailCopy, roofCopy];
  }, [type, lod]); // 移除width和height依赖，在后面单独处理
  // 使用缓存的随机值
  const hasExtraFeatures = randomRef.current.hasExtraFeatures;
  
  // 建筑物高度随机变化 - 使用缓存的随机因子
  const actualHeight = height * randomRef.current.heightFactor;
  
  return (
    <group position={[position[0], 0, position[1]]}>
      {/* 主体建筑 */}
      <mesh position={[0, actualHeight / 2, 0]}>
        <boxGeometry args={[width, actualHeight, depth]} />
        <meshStandardMaterial
          map={mainTexture}
          roughness={0.7}
          metalness={0.2}
        />
      </mesh>
      
      {/* 建筑物屋顶 */}
      <mesh position={[0, actualHeight + 0.5, 0]}>
        <boxGeometry args={[width * 0.9, 1, depth * 0.9]} />
        <meshStandardMaterial
          map={roofTexture}
          roughness={0.8}
          metalness={0.1}
        />
      </mesh>
      
      {/* 窗户和细节 - 只在高细节模式和有细节纹理时添加 */}
      {lod !== 'low' && detailTexture && (
        <>
          {/* 前面窗户 */}
          <mesh position={[0, actualHeight / 2, depth / 2 + 0.01]}>
            <planeGeometry args={[width * 0.8, actualHeight * 0.8]} />
            <meshStandardMaterial
              map={detailTexture}
              transparent={true}
              opacity={0.9}
              roughness={0.2}
              metalness={0.8}
            />
          </mesh>
          
          {/* 后面窗户 */}
          <mesh position={[0, actualHeight / 2, -depth / 2 - 0.01]} rotation={[0, Math.PI, 0]}>
            <planeGeometry args={[width * 0.8, actualHeight * 0.8]} />
            <meshStandardMaterial
              map={detailTexture}
              transparent={true}
              opacity={0.9}
              roughness={0.2}
              metalness={0.8}
            />
          </mesh>
          
          {/* 侧面窗户 */}
          <mesh position={[width / 2 + 0.01, actualHeight / 2, 0]} rotation={[0, Math.PI / 2, 0]}>
            <planeGeometry args={[depth * 0.8, actualHeight * 0.8]} />
            <meshStandardMaterial
              map={detailTexture}
              transparent={true}
              opacity={0.9}
              roughness={0.2}
              metalness={0.8}
            />
          </mesh>
          
          <mesh position={[-width / 2 - 0.01, actualHeight / 2, 0]} rotation={[0, -Math.PI / 2, 0]}>
            <planeGeometry args={[depth * 0.8, actualHeight * 0.8]} />
            <meshStandardMaterial
              map={detailTexture}
              transparent={true}
              opacity={0.9}
              roughness={0.2}
              metalness={0.8}
            />
          </mesh>
        </>
      )}
      
      {/* 额外特征 - 天线、水箱等 */}
      {hasExtraFeatures && lod !== 'low' && (
        <group position={[0, actualHeight + 1.5, 0]}>
          {/* 水箱或其他屋顶结构 */}
          {randomRef.current.extraFeatures.hasWaterTank && (
            <mesh position={[width * 0.3, 0, depth * 0.3]}>
              <cylinderGeometry args={[1, 1, 2, 8]} />
              <meshStandardMaterial color="#78909C" roughness={0.8} />
            </mesh>
          )}
          
          {/* 天线 */}
          {randomRef.current.extraFeatures.hasAntenna && (
            <mesh position={[-width * 0.3, 1, -depth * 0.3]}>
              <cylinderGeometry args={[0.1, 0.1, 4, 4]} />
              <meshStandardMaterial color="#9E9E9E" metalness={0.8} />
            </mesh>
          )}
        </group>
      )}
    </group>
  );
};

// 道路组件 - 使用动态生成的纹理
export const EnhancedRoad = ({ position, length, width, rotation, type = 'normal' }) => {
  // 使用useRef存储道路随机特性
  const roadRandomRef = useRef({
    initialized: false
  });
  
  if (!roadRandomRef.current.initialized) {
    roadRandomRef.current.initialized = true;
  }
  
  // 根据道路类型生成不同的纹理 - 使用缓存
  const texture = useMemo(() => {
    let roadTexture;
    
    switch(type) {
      case 'highway':
        roadTexture = getCachedTexture('highway', 'default', () =>
          TextureGenerator.generateHighwayTexture());
        break;
      case 'primary':
        roadTexture = getCachedTexture('road', 'primary', () =>
          TextureGenerator.generateRoadTexture('#505050', '#FFEB3B'));
        break;
      default:
        roadTexture = getCachedTexture('road', 'normal', () =>
          TextureGenerator.generateRoadTexture());
        break;
    }
    
    // 复制纹理并设置重复
    const textureCopy = roadTexture.clone();
    textureCopy.wrapS = textureCopy.wrapT = THREE.RepeatWrapping;
    textureCopy.repeat.set(length/10, width/10);
    
    return textureCopy;
  }, [type]); // 移除length和width依赖
  
  return (
    <mesh
      position={[position[0], 0.02, position[1]]}
      rotation={[-Math.PI / 2, 0, rotation || 0]}
    >
      <planeGeometry args={[length, width]} />
      <meshStandardMaterial 
        map={texture} 
        side={THREE.DoubleSide}
      />
    </mesh>
  );
};

// 交通信号灯组件
export const TrafficLight = ({ position }) => {
  return (
    <group position={[position[0], 0, position[1]]}>
      {/* 灯杆 */}
      <mesh position={[0, 2, 0]}>
        <cylinderGeometry args={[0.1, 0.1, 4, 8]} />
        <meshStandardMaterial color="#444444" />
      </mesh>
      
      {/* 信号灯箱体 */}
      <mesh position={[0, 4, 0]}>
        <boxGeometry args={[0.3, 0.9, 0.3]} />
        <meshStandardMaterial color="#222222" />
      </mesh>
      
      {/* 红灯 */}
      <mesh position={[0, 4.3, 0.2]}>
        <sphereGeometry args={[0.1, 16, 16]} />
        <meshStandardMaterial color="red" emissive="red" emissiveIntensity={0.5} />
      </mesh>
      
      {/* 黄灯 */}
      <mesh position={[0, 4.0, 0.2]}>
        <sphereGeometry args={[0.1, 16, 16]} />
        <meshStandardMaterial color="yellow" emissive="yellow" emissiveIntensity={0.3} />
      </mesh>
      
      {/* 绿灯 */}
      <mesh position={[0, 3.7, 0.2]}>
        <sphereGeometry args={[0.1, 16, 16]} />
        <meshStandardMaterial color="green" emissive="green" emissiveIntensity={0.5} />
      </mesh>
    </group>
  );
};

// 树木组件
export const Tree = ({ position, height = 5, type = 'normal' }) => {
  // 树干颜色
  const trunkColor = useMemo(() => {
    return type === 'pine' ? '#5D4037' : '#795548';
  }, [type]);
  
  // 树叶颜色
  const leavesColor = useMemo(() => {
    switch(type) {
      case 'pine': return '#2E7D32';
      case 'autumn': return '#FF9800';
      case 'cherry': return '#F8BBD0';
      default: return '#4CAF50';
    }
  }, [type]);
  
  return (
    <group position={[position[0], 0, position[1]]}>
      {/* 树干 */}
      <mesh position={[0, height/2, 0]}>
        <cylinderGeometry args={[0.2, 0.3, height, 8]} />
        <meshStandardMaterial color={trunkColor} />
      </mesh>
      
      {/* 树叶 */}
      {type === 'pine' ? (
        // 松树形状
        <mesh position={[0, height, 0]}>
          <coneGeometry args={[1.5, height, 8]} />
          <meshStandardMaterial color={leavesColor} />
        </mesh>
      ) : (
        // 普通树形状
        <mesh position={[0, height + 1, 0]}>
          <sphereGeometry args={[2, 16, 16]} />
          <meshStandardMaterial color={leavesColor} />
        </mesh>
      )}
    </group>
  );
};

// 街道家具组件 (长椅、垃圾桶等)
export const StreetFurniture = ({ position, type = 'bench' }) => {
  switch(type) {
    case 'bench':
      return (
        <group position={[position[0], 0, position[1]]}>
          {/* 长椅座位 */}
          <mesh position={[0, 0.4, 0]}>
            <boxGeometry args={[1.5, 0.1, 0.5]} />
            <meshStandardMaterial color="#A1887F" />
          </mesh>
          
          {/* 长椅腿 */}
          {[[-0.6, 0, 0], [0.6, 0, 0]].map((pos, i) => (
            <mesh key={i} position={[position[0] + pos[0], 0.2, position[1] + pos[2]]}>
              <boxGeometry args={[0.1, 0.4, 0.5]} />
              <meshStandardMaterial color="#5D4037" />
            </mesh>
          ))}
        </group>
      );
    
    case 'trashcan':
      return (
        <group position={[position[0], 0, position[1]]}>
          <mesh position={[0, 0.5, 0]}>
            <cylinderGeometry args={[0.3, 0.25, 1, 8]} />
            <meshStandardMaterial color="#455A64" />
          </mesh>
        </group>
      );
      
    case 'streetlamp':
      return (
        <group position={[position[0], 0, position[1]]}>
          {/* 灯杆 */}
          <mesh position={[0, 1.5, 0]}>
            <cylinderGeometry args={[0.05, 0.08, 3, 8]} />
            <meshStandardMaterial color="#616161" />
          </mesh>
          
          {/* 灯头 */}
          <mesh position={[0, 3, 0]}>
            <sphereGeometry args={[0.2, 16, 16]} />
            <meshStandardMaterial color="#FFECB3" emissive="#FFECB3" emissiveIntensity={0.5} />
          </mesh>
          
          {/* 灯光 */}
          <pointLight position={[0, 3, 0]} intensity={0.5} distance={5} color="#FFECB3" />
        </group>
      );
      
    default:
      return null;
  }
};

export default {
  EnhancedBuilding,
  EnhancedRoad,
  TrafficLight,
  Tree,
  StreetFurniture
};