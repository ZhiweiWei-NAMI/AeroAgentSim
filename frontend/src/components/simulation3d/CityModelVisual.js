import React, { useMemo, useCallback, useRef, useEffect } from 'react';
import PropTypes from 'prop-types';
import * as THREE from 'three';
import { useFrame } from '@react-three/fiber';
import { LoadingIndicator, ErrorIndicator } from './CityComponents';
import { EnhancedBuilding, EnhancedRoad, TrafficLight, Tree, StreetFurniture } from './CityElements';
import TextureGenerator from './TextureGenerator';

// 单个云朵组件
const CloudPuff = ({ position, scale, rotation }) => {
  return (
    <group position={position} rotation={rotation} scale={scale}>
      <mesh position={[0, 0, 0]}>
        <sphereGeometry args={[1, 7, 7]} />
        <meshStandardMaterial color="white" transparent opacity={0.8} />
      </mesh>
      <mesh position={[1, 0.2, 0]}>
        <sphereGeometry args={[0.8, 6, 6]} />
        <meshStandardMaterial color="white" transparent opacity={0.9} />
      </mesh>
      <mesh position={[-0.8, 0.1, 0.5]}>
        <sphereGeometry args={[0.7, 6, 6]} />
        <meshStandardMaterial color="white" transparent opacity={0.6} />
      </mesh>
      <mesh position={[0.2, -0.2, -0.8]}>
        <sphereGeometry args={[0.6, 6, 6]} />
        <meshStandardMaterial color="white" transparent opacity={0.7} />
      </mesh>
    </group>
  );
};

// 云朵集合组件
const Clouds = ({ count = 15, height = 80, spread = 200, timeOfDay = 12 }) => {
  const cloudsRef = useRef();
  const cloudPuffs = useRef([]);
  
  // 根据时间调整云朵亮度
  const isNight = timeOfDay < 6 || timeOfDay > 18;
  const cloudBrightness = isNight ? 0.6 : 1;
  const cloudOpacity = isNight ? 0.5 : 0.8;
  
  // 初始化云朵位置
  useEffect(() => {
    cloudPuffs.current = Array(count).fill().map(() => ({
      position: [
        (Math.random() - 0.5) * spread,
        height + (Math.random() - 0.5) * 20,
        (Math.random() - 0.5) * spread
      ],
      rotation: [0, Math.random() * Math.PI * 2, 0],
      scale: 2 + Math.random() * 4,
      speed: 0.05 + Math.random() * 0.1
    }));
  }, [count, height, spread]);
  
  // 云朵漂浮动画
  useFrame((state, delta) => {
    if (cloudsRef.current) {
      cloudsRef.current.children.forEach((cloud, i) => {
        const data = cloudPuffs.current[i];
        // 缓慢移动云朵
        cloud.position.x += data.speed * delta;
        
        // 如果云朵移出范围，将其移到另一侧
        if (cloud.position.x > spread / 2) {
          cloud.position.x = -spread / 2;
          cloud.position.z = (Math.random() - 0.5) * spread;
        }
      });
    }
  });
  
  return (
    <group ref={cloudsRef}>
      {cloudPuffs.current.map((data, i) => (
        <CloudPuff
          key={i}
          position={data.position}
          rotation={data.rotation}
          scale={data.scale}
        />
      ))}
    </group>
  );
};

// 城市模型视觉组件 - 负责渲染3D元素
const CityModelVisual = React.memo(React.forwardRef(({
  mapStyle = 'standard',
  buildings = [],
  roads = [],
  loading = false,
  error = null,
  visibleBuildings = {},
  timeOfDay = 12
}, ref) => {

  // 根据地图样式设置地面颜色
  const getGroundColor = useCallback(() => {
    switch(mapStyle) {
      case 'satellite': return '#1a3d0c';
      case 'dark': return '#111111';
      default: return '#cccccc';
    }
  }, [mapStyle]);

  // 使用 useMemo 来优化地面材质的创建
  const groundMaterial = useMemo(() => {
    return new THREE.MeshStandardMaterial({
      color: getGroundColor(),
      side: THREE.DoubleSide
    });
  }, [getGroundColor]);

  // 生成一些随机的树、街道家具和草地
  const cityDecorations = useMemo(() => {
    const decorations = [];
    const citySize = 1000; // 假设城市大小为1000x1000
    const decorationCount = 200; // 增加装饰物数量
    
    // 生成随机树
    for (let i = 0; i < decorationCount; i++) {
      const x = (Math.random() - 0.5) * citySize;
      const z = (Math.random() - 0.5) * citySize;
      const height = 3 + Math.random() * 3; // 3-6米高
      const type = Math.random() > 0.7 ? 'pine' : 'normal'; // 30%的松树
      
      decorations.push({
        type: 'tree',
        position: [x, z],
        height,
        treeType: type,
        id: `tree-${i}`
      });
    }
    
    // 生成一些草地区域
    for (let i = 0; i < 30; i++) {
      const x = (Math.random() - 0.5) * citySize;
      const z = (Math.random() - 0.5) * citySize;
      const size = 20 + Math.random() * 50; // 草地大小
      
      decorations.push({
        type: 'grass',
        position: [x, z],
        size: size,
        id: `grass-${i}`
      });
    }
    
    // 生成一些街灯
    for (let i = 0; i < decorationCount / 4; i++) {
      const x = (Math.random() - 0.5) * citySize;
      const z = (Math.random() - 0.5) * citySize;
      
      decorations.push({
        type: 'streetlamp',
        position: [x, z],
        id: `lamp-${i}`
      });
    }
    
    // 生成一些交通灯
    for (let i = 0; i < decorationCount / 8; i++) {
      const x = (Math.random() - 0.5) * citySize;
      const z = (Math.random() - 0.5) * citySize;
      
      decorations.push({
        type: 'trafficlight',
        position: [x, z],
        id: `traffic-${i}`
      });
    }
    
    // 生成一些小型装饰物（垃圾桶、长椅等）
    for (let i = 0; i < decorationCount / 6; i++) {
      const x = (Math.random() - 0.5) * citySize;
      const z = (Math.random() - 0.5) * citySize;
      const decorType = Math.random() > 0.5 ? 'bench' : 'trashbin';
      
      decorations.push({
        type: 'furniture',
        furnitureType: decorType,
        position: [x, z],
        id: `furniture-${i}`
      });
    }
    
    return decorations;
  }, []);

  // 创建草地纹理
  const grassTexture = useMemo(() => {
    return TextureGenerator.generateGrassTexture('#4CAF50');
  }, []);

  return (
    <group ref={ref}> {/* 将ref附加到group上 */}
      {/* 添加云朵 */}
      <Clouds count={20} height={100} spread={300} timeOfDay={timeOfDay} />
      {/* 地面 - 使用更复杂的地形 */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.1, 0]}>
        <planeGeometry args={[2000, 2000, 100, 100]} />
        <meshStandardMaterial
          color={getGroundColor()}
          side={THREE.DoubleSide}
          roughness={0.8}
          metalness={0.2}
          displacementScale={0.5}
        />
      </mesh>

      {/* 从OpenStreetMap获取的建筑物 - 使用增强的建筑物组件 */}
      {Object.values(visibleBuildings).map((building) => (
        <EnhancedBuilding
          key={`building-${building.id}`}
          position={[building.position[0], building.position[1]]}
          width={building.lod === 'low' ? Math.max(building.width, 10) : building.width}
          height={building.lod === 'low' ? Math.max(building.height, 5) : building.height}
          depth={building.lod === 'low' ? Math.max(building.depth, 10) : building.depth}
          type={building.type || 'residential'}
          lod={building.lod}
        />
      ))}

      {/* 从OpenStreetMap获取的道路 - 使用增强的道路组件 */}
      {roads.map((road, i) => (
        <EnhancedRoad
          key={`osm-road-${road.id || i}`}
          position={[road.position[0], road.position[1]]}
          length={road.length}
          width={road.width}
          rotation={road.rotation || 0}
          type={road.highway === 'primary' ? 'primary' : 'normal'}
        />
      ))}
      
      {/* 装饰物：草地、树木、街灯、交通灯等 */}
      {cityDecorations.map((decoration) => {
        if (decoration.type === 'grass') {
          // 草地区域
          return (
            <mesh
              key={decoration.id}
              position={[decoration.position[0], -0.05, decoration.position[1]]}
              rotation={[-Math.PI / 2, 0, 0]}
            >
              <planeGeometry args={[decoration.size, decoration.size]} />
              <meshStandardMaterial
                map={grassTexture}
                color="#4CAF50"
                roughness={0.9}
              />
            </mesh>
          );
        } else if (decoration.type === 'tree') {
          return (
            <Tree
              key={decoration.id}
              position={decoration.position}
              height={decoration.height}
              type={decoration.treeType}
            />
          );
        } else if (decoration.type === 'streetlamp') {
          return (
            <StreetFurniture
              key={decoration.id}
              position={decoration.position}
              type="streetlamp"
            />
          );
        } else if (decoration.type === 'furniture') {
          return (
            <StreetFurniture
              key={decoration.id}
              position={decoration.position}
              type={decoration.furnitureType}
            />
          );
        } else if (decoration.type === 'trafficlight') {
          return (
            <TrafficLight
              key={decoration.id}
              position={decoration.position}
            />
          );
        }
        return null;
      })}

      {/* 加载状态和错误指示器 */}
      {loading && <LoadingIndicator />}
      {error && <ErrorIndicator />}
    </group>
  );
}));

// PropTypes 定义
CityModelVisual.propTypes = {
  mapStyle: PropTypes.oneOf(['standard', 'satellite', 'dark']),
  buildings: PropTypes.array, // 虽然现在主要用visibleBuildings，但保留以备将来使用
  roads: PropTypes.array,
  loading: PropTypes.bool,
  error: PropTypes.object,
  visibleBuildings: PropTypes.object,
  timeOfDay: PropTypes.number // 添加时间属性，用于云朵效果
};

export default CityModelVisual;