import * as THREE from 'three';

// 纹理生成器类 - 用于在运行时生成基本纹理
class TextureGenerator {
  // 生成砖墙纹理
  static generateBrickTexture(color = '#a52a2a', brickColor = '#8b4513', size = 512) {
    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext('2d');
    
    // 背景色
    ctx.fillStyle = color;
    ctx.fillRect(0, 0, size, size);
    
    // 砖块参数
    const brickWidth = size / 8;
    const brickHeight = size / 16;
    const mortarSize = size / 64;
    
    // 绘制砖块
    ctx.fillStyle = brickColor;
    
    // 偶数行
    for (let y = 0; y < size; y += brickHeight + mortarSize) {
      for (let x = 0; x < size; x += brickWidth + mortarSize) {
        ctx.fillRect(x, y, brickWidth, brickHeight);
      }
    }
    
    // 奇数行（错开）
    for (let y = brickHeight + mortarSize; y < size; y += brickHeight + mortarSize) {
      for (let x = brickWidth / 2; x < size; x += brickWidth + mortarSize) {
        ctx.fillRect(x, y, brickWidth, brickHeight);
      }
    }
    
    // 创建Three.js纹理
    const texture = new THREE.CanvasTexture(canvas);
    texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
    
    return texture;
  }
  
  // 生成窗户纹理
  static generateWindowTexture(color = '#87CEEB', frameColor = '#FFFFFF', size = 512) {
    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext('2d');
    
    // 背景色（窗户）
    ctx.fillStyle = color;
    ctx.fillRect(0, 0, size, size);
    
    // 窗框
    const frameWidth = size / 16;
    ctx.fillStyle = frameColor;
    
    // 水平窗框
    ctx.fillRect(0, 0, size, frameWidth);
    ctx.fillRect(0, size / 2 - frameWidth / 2, size, frameWidth);
    ctx.fillRect(0, size - frameWidth, size, frameWidth);
    
    // 垂直窗框
    ctx.fillRect(0, 0, frameWidth, size);
    ctx.fillRect(size / 2 - frameWidth / 2, 0, frameWidth, size);
    ctx.fillRect(size - frameWidth, 0, frameWidth, size);
    
    // 创建Three.js纹理
    const texture = new THREE.CanvasTexture(canvas);
    texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
    
    return texture;
  }
  
  // 生成道路纹理
  static generateRoadTexture(color = '#333333', lineColor = '#FFFFFF', size = 512) {
    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext('2d');
    
    // 背景色（道路）
    ctx.fillStyle = color;
    ctx.fillRect(0, 0, size, size);
    
    // 道路标记线
    ctx.fillStyle = lineColor;
    
    // 中心虚线
    const dashLength = size / 16;
    const dashGap = size / 16;
    const lineWidth = size / 32;
    
    for (let y = 0; y < size; y += dashLength + dashGap) {
      ctx.fillRect(size / 2 - lineWidth / 2, y, lineWidth, dashLength);
    }
    
    // 创建Three.js纹理
    const texture = new THREE.CanvasTexture(canvas);
    texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
    
    return texture;
  }
  
  // 生成高速公路纹理
  static generateHighwayTexture(color = '#505050', lineColor = '#FFFFFF', size = 512) {
    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext('2d');
    
    // 背景色（道路）
    ctx.fillStyle = color;
    ctx.fillRect(0, 0, size, size);
    
    // 道路标记线
    ctx.fillStyle = lineColor;
    
    // 两侧实线
    const lineWidth = size / 32;
    const sideLineOffset = size / 4;
    
    // 左侧实线
    ctx.fillRect(sideLineOffset - lineWidth / 2, 0, lineWidth, size);
    
    // 右侧实线
    ctx.fillRect(size - sideLineOffset - lineWidth / 2, 0, lineWidth, size);
    
    // 中心虚线
    const dashLength = size / 16;
    const dashGap = size / 16;
    
    for (let y = 0; y < size; y += dashLength + dashGap) {
      ctx.fillRect(size / 2 - lineWidth / 2, y, lineWidth, dashLength);
    }
    
    // 创建Three.js纹理
    const texture = new THREE.CanvasTexture(canvas);
    texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
    
    return texture;
  }
  
  // 生成简单的草地纹理
  static generateGrassTexture(color = '#4CAF50', size = 512) {
    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext('2d');
    
    // 背景色
    ctx.fillStyle = color;
    ctx.fillRect(0, 0, size, size);
    
    // 添加一些随机的草叶
    for (let i = 0; i < 1000; i++) {
      const x = Math.random() * size;
      const y = Math.random() * size;
      const length = 2 + Math.random() * 5;
      const width = 1 + Math.random() * 2;
      const angle = Math.random() * Math.PI;
      
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(angle);
      
      // 草叶颜色略微变化
      const r = parseInt(color.slice(1, 3), 16);
      const g = parseInt(color.slice(3, 5), 16);
      const b = parseInt(color.slice(5, 7), 16);
      
      const variation = 30;
      const newR = Math.max(0, Math.min(255, r + (Math.random() - 0.5) * variation));
      const newG = Math.max(0, Math.min(255, g + (Math.random() - 0.5) * variation));
      const newB = Math.max(0, Math.min(255, b + (Math.random() - 0.5) * variation));
      
      ctx.fillStyle = `rgb(${newR}, ${newG}, ${newB})`;
      ctx.fillRect(-width / 2, -length / 2, width, length);
      
      ctx.restore();
    }
    
    // 创建Three.js纹理
    const texture = new THREE.CanvasTexture(canvas);
    texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
    
    return texture;
  }
  
  // 生成简单的商业建筑纹理
  static generateCommercialBuildingTexture(color = '#607D8B', windowColor = '#B3E5FC', size = 512) {
    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext('2d');
    
    // 背景色
    ctx.fillStyle = color;
    ctx.fillRect(0, 0, size, size);
    
    // 窗户参数
    const windowSize = size / 8;
    const windowGap = size / 16;
    const windowsPerRow = Math.floor(size / (windowSize + windowGap));
    
    // 绘制窗户网格
    ctx.fillStyle = windowColor;
    
    for (let row = 0; row < windowsPerRow; row++) {
      for (let col = 0; col < windowsPerRow; col++) {
        const x = col * (windowSize + windowGap) + windowGap;
        const y = row * (windowSize + windowGap) + windowGap;
        
        // 随机决定窗户是否亮着
        if (Math.random() > 0.3) {
          ctx.fillRect(x, y, windowSize, windowSize);
        }
      }
    }
    
    // 创建Three.js纹理
    const texture = new THREE.CanvasTexture(canvas);
    texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
    
    return texture;
  }
}

export default TextureGenerator;