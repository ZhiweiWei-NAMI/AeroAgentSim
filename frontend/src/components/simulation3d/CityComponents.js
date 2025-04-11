import React from 'react';
import * as THREE from 'three';

// 加载指示器组件
export const LoadingIndicator = () => (
  <mesh position={[0, 10, 0]}>
    <sphereGeometry args={[2, 16, 16]} />
    <meshStandardMaterial color="red" wireframe />
  </mesh>
);

// 错误指示器组件
export const ErrorIndicator = () => (
  <mesh position={[0, 10, 0]}>
    <boxGeometry args={[4, 4, 4]} />
    <meshStandardMaterial color="red" />
  </mesh>
);