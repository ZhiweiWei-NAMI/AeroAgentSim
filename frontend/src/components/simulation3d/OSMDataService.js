import axios from 'axios';

// 缓存已加载的OSM数据
export const osmDataCache = {};

// 从OpenStreetMap获取建筑物数据
export const fetchOSMData = async (center, radius) => {
  try {
    console.log('构建Overpass API查询，中心点:', center, '半径(km):', radius);
    
    // 构建Overpass API查询
    const query = `
      [out:json];
      (
        way["building"](around:${radius * 1000},${center[0]},${center[1]});
        way["highway"](around:${radius * 1000},${center[0]},${center[1]});
      );
      out body;
      >;
      out skel qt;
    `;
    
    console.log('Overpass API查询:', query);
    
    // 发送请求到Overpass API，添加超时设置
    const response = await axios.post('https://overpass-api.de/api/interpreter', query, {
      timeout: 30000, // 30秒超时
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded'
      }
    });
    
    console.log('Overpass API响应状态:', response.data);
    
    // 处理响应数据
    const buildings = [];
    const roads = [];
    const nodes = {};
    
    // 首先处理所有节点
    response.data.elements.forEach(element => {
      if (element.type === 'node') {
        // 近似将经纬度差转换为米 (适用于小范围)
        // X 对应经度差, Z 对应纬度差 (Three.js Y轴向上)
        const latRad = center[0] * Math.PI / 180; // 中心点纬度转弧度
        nodes[element.id] = [
          (element.lon - center[1]) * 111132 * Math.cos(latRad), // X坐标
          (element.lat - center[0]) * 111132  // Z坐标
        ];
      }
    });
    
    // 然后处理建筑物和道路
    response.data.elements.forEach(element => {
      if (element.type === 'way') {
        if (element.tags && element.tags.building) {
          // 处理建筑物
          const nodePositions = element.nodes.map(nodeId => nodes[nodeId]);
          if (nodePositions.length > 0) {
            // 计算建筑物中心点
            const center = nodePositions.reduce(
              (acc, pos) => [acc[0] + pos[0] / nodePositions.length, acc[1] + pos[1] / nodePositions.length],
              [0, 0]
            );
            
            // 计算建筑物尺寸
            const minX = Math.min(...nodePositions.map(p => p[0]));
            const maxX = Math.max(...nodePositions.map(p => p[0]));
            const minY = Math.min(...nodePositions.map(p => p[1]));
            const maxY = Math.max(...nodePositions.map(p => p[1]));
            
            const width = maxX - minX;
            const depth = maxY - minY;
            
            // 根据建筑物类型估计高度
            let height = 10; // 默认高度
            if (element.tags.height) {
              height = parseFloat(element.tags.height);
            } else if (element.tags.building === 'apartments') {
              height = 15 + Math.random() * 10;
            } else if (element.tags.building === 'commercial') {
              height = 20 + Math.random() * 15;
            } else if (element.tags.building === 'industrial') {
              height = 8 + Math.random() * 7;
            } else if (element.tags.building === 'house') {
              height = 5 + Math.random() * 3;
            } else if (element.tags.building === 'yes') {
              // 判断是否有levels
              if (element.tags.building.levels) {
                height = parseInt(element.tags.levels) * 3; // 每层3米
              } else {
                height = 10 + Math.random() * 3; // 随机高度
              }
            } else if (element.tags.building === 'wall') {
              height = 3 + Math.random() * 2; // 随机高度
            } else if (element.tags.building === 'restaurant') {
              height = 5 + Math.random() * 3; // 随机高度
            } else if (element.tags.building === 'school') {
              height = 10 + Math.random() * 5; // 随机高度
            } else if (element.tags.building === 'hospital') {
              height = 15 + Math.random() * 10; // 随机高度
            }
            // console.log(center);
            buildings.push({
              id: element.id,
              position: center,
              width: Math.max(width, 5),
              height: height,
              depth: Math.max(depth, 5),
              tags: element.tags
            });
          }
        } else if (element.tags && element.tags.highway) {
          // 处理道路
          const nodePositions = element.nodes.map(nodeId => nodes[nodeId]);
          if (nodePositions.length > 1) {
            // 简化为直线道路（实际项目中应使用更复杂的线段处理）
            const start = nodePositions[0];
            const end = nodePositions[nodePositions.length - 1];
            
            // 计算道路中心点、长度和旋转角度
            const center = [
              (start[0] + end[0]) / 2,
              (start[1] + end[1]) / 2
            ];
            
            const dx = end[0] - start[0];
            const dy = end[1] - start[1];
            const length = Math.sqrt(dx * dx + dy * dy);
            const rotation = Math.atan2(dy, dx);
            
            // 根据道路类型设置宽度
            let width = 3;
            if (element.tags.highway === 'primary') width = 8;
            else if (element.tags.highway === 'secondary') width = 6;
            else if (element.tags.highway === 'tertiary') width = 5;
            else if (element.tags.highway === 'residential') width = 4;
            
            roads.push({
              id: element.id,
              position: center,
              length: length,
              width: width,
              rotation: rotation,
              highway: element.tags.highway
            });
          }
        }
      }
    });
    
    return { buildings, roads };
  } catch (error) {
    console.error('获取OpenStreetMap数据失败:', error);
    throw error;
  }
};

// 兼容性处理：如果浏览器不支持requestIdleCallback
export const setupIdleCallback = () => {
  if (typeof window !== 'undefined' && !('requestIdleCallback' in window)) {
    window.requestIdleCallback = function(callback) {
      return setTimeout(function() {
        const start = Date.now();
        callback({
          didTimeout: false,
          timeRemaining: function() {
            return Math.max(0, 50 - (Date.now() - start));
          }
        });
      }, 1);
    };
  
    window.cancelIdleCallback = function(id) {
      clearTimeout(id);
    };
  }
};