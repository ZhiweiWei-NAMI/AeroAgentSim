import React, { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { gsap } from 'gsap';

// 代理控制器组件 - 提供通用的代理管理功能
const AgentController = ({
  agents = [], // 假设 agents 包含 { id, position, posRef, ... }
               // position 是从后端获取的目标位置
  type = 'vehicle',
  onPositionUpdate = () => {},
  enabled = true,
  updateInterval = 0, // 0表示使用requestAnimationFrame
  boundary = 5000,
  syncWithBackend = false,
  lastSyncTimestamp = null, // 当前 agents 数据获取的时间戳
  syncInterval = 2000, // 动画应在此时间内完成
}) => {
  // 使用 Map 存储代理状态，键为agent.id
  const agentDataMapRef = useRef(new Map());
  const animationFrameId = useRef(null);
  const intervalId = useRef(null);
  const isAnimationLoopRunning = useRef(false); // 跟踪动画循环是否已启动
  const lastProcessedTimestampRef = useRef(lastSyncTimestamp); // 存储上次处理的时间戳

  // 当 agents prop 更新时，更新 Map 中的数据
  useEffect(() => {
    const currentMap = agentDataMapRef.current;
    const newAgentIds = new Set(agents.map(a => a.id));

    // 更新或添加 agent 数据
    agents.forEach(agent => {
      const agentId = agent.id;
      const existingData = currentMap.get(agentId);

      // 确保 position 存在且是数组
      const position = Array.isArray(agent.position) && agent.position.length === 3
                           ? agent.position
                           : (existingData?.posRef ? [existingData.posRef.x, existingData.posRef.y, existingData.posRef.z] : [0, 0, 0]);

      // 确保 posRef 存在
      const posRef = existingData?.posRef || agent.posRef || new THREE.Vector3(...position);

      currentMap.set(agentId, {
        ...(existingData || {}), // 保留现有的动画状态等
        ...agent, // 更新其他 agent 属性
        position: position, // 存储最新的位置
        posRef: posRef, // 存储或更新 posRef
      });
    });

    // 移除不再存在的 agents
    currentMap.forEach((data, agentId) => {
      if (!newAgentIds.has(agentId)) {
        // 清理动画
        if (data.animation) {
          data.animation.kill();
        }
        currentMap.delete(agentId);
        console.log(`移除了 Agent ${agentId}`);
      }
    });

  }, [agents]); // 依赖于 agents prop

  // 当接收到新的同步时间戳时，为每个 agent 设置动画目标
  useEffect(() => {
    // 仅当时间戳实际更新时执行
    if (lastSyncTimestamp && lastSyncTimestamp !== lastProcessedTimestampRef.current) {
      console.log(`同步触发: 时间戳 ${lastSyncTimestamp}`);
      lastProcessedTimestampRef.current = lastSyncTimestamp; // 更新已处理的时间戳

      agentDataMapRef.current.forEach((agentData, agentId) => {
        // 1. 停止当前可能正在进行的动画
        if (agentData.animation) {
          console.log(`Agent ${agentId}: 停止旧动画`);
          agentData.animation.kill();
          agentData.animation = null;
        }

        // 2. 记录动画的起点（当前 posRef 的位置）
        if (!agentData.posRef) {
          console.warn(`Agent ${agentId}: posRef 不存在，无法设置动画起点`);
          return; // 跳过此 agent
        }
        
        // 保存当前位置作为动画起点
        agentData.animationStartPos = [agentData.posRef.x, agentData.posRef.y, agentData.posRef.z];
        
        // 3. 记录动画的终点（最新的 position）
        if (!Array.isArray(agentData.position) || agentData.position.length !== 3) {
          console.warn(`Agent ${agentId}: position 无效 (${agentData.position})，无法设置动画终点`);
          agentData.animationEndPos = [...agentData.animationStartPos]; // 将终点设为起点，避免动画错误
        } else {
          agentData.animationEndPos = [...agentData.position];
        }

        // console.log(`Agent ${agentId}: 准备动画 从 ${agentData.animationStartPos.map(n=>n.toFixed(2))} 到 ${agentData.animationEndPos.map(n=>n.toFixed(2))}`);

        // 4. 标记需要创建/更新动画
        agentData.needsAnimationUpdate = true;
      });
    }
  }, [lastSyncTimestamp]); // 依赖于 lastSyncTimestamp

  // 动画循环 Effect
  useEffect(() => {
    // 条件：启用、后端同步、有代理数据
    if (!enabled || !syncWithBackend || agentDataMapRef.current.size === 0) {
      // 如果条件不满足，确保停止动画循环
      if (animationFrameId.current) {
        cancelAnimationFrame(animationFrameId.current);
        animationFrameId.current = null;
      }
      if (intervalId.current) {
        clearInterval(intervalId.current);
        intervalId.current = null;
      }
      // 杀死所有相关的 GSAP 动画
      const posRefsToKill = Array.from(agentDataMapRef.current.values())
                                  .map(d => d.posRef)
                                  .filter(Boolean);
      if (posRefsToKill.length > 0) {
        gsap.killTweensOf(posRefsToKill);
      }
      isAnimationLoopRunning.current = false;
      console.log("动画循环停止：条件不满足");
      return; // 退出 effect
    }

    // 防止重复启动动画循环
    if (isAnimationLoopRunning.current) {
      return;
    }

    console.log("启动动画循环");

    // --- 动画循环核心逻辑 ---
    const runAnimationFrame = () => {
      gsap.ticker.tick(); // 更新 GSAP 时钟

      const updatedAgentsForCallback = []; // 收集需要回调的数据

      agentDataMapRef.current.forEach((agentData, agentId) => {
        // 检查是否需要创建动画
        if (agentData.needsAnimationUpdate && agentData.animationStartPos && agentData.animationEndPos) {
          const startPos = agentData.animationStartPos;
          const endPos = agentData.animationEndPos;

          const dx = endPos[0] - startPos[0];
          const dz = endPos[2] - startPos[2];
          const movementDist = Math.sqrt(dx*dx + dz*dz);

          console.log(`Agent ${agentId}: 检查动画创建，距离: ${movementDist.toFixed(4)}`);

          // 确保 posRef 存在
          if (!agentData.posRef) {
            console.warn(`Agent ${agentId}: posRef 不存在，无法创建动画`);
            agentData.needsAnimationUpdate = false; // 标记为已处理（虽然失败）
            return; // 跳过此 agent
          }

          if (movementDist > 0.001) { // 移动阈值
            agentData.angle = Math.atan2(dz, dx); // 计算角度

            console.log(`Agent ${agentId}: 创建 GSAP 动画`);
            agentData.animation = gsap.to(agentData.posRef, {
              x: endPos[0],
              y: endPos[1],
              z: endPos[2],
              duration: syncInterval / 1000,
              ease: "linear", // 或者 "power1.out"
              onComplete: () => {
                console.log(`Agent ${agentId}: 动画完成`);
                agentData.animation = null; // 清除动画引用
              },
              // onUpdate 不再直接调用 onPositionUpdate，在循环末尾统一调用
            });
          } else {
            // 距离过小，直接设置为目标位置
            console.log(`Agent ${agentId}: 距离过小，直接设置位置`);
            agentData.posRef.set(endPos[0], endPos[1], endPos[2]);
            agentData.angle = agentData.angle || 0; // 保留之前的角度或设为0
          }
          agentData.needsAnimationUpdate = false; // 标记为已处理
        }

        // 收集当前状态用于回调 (确保 posRef 存在)
        if (agentData.posRef) {
          updatedAgentsForCallback.push({
            // 只包含父组件关心的字段
            id: agentId,
            position: [agentData.posRef.x, agentData.posRef.y, agentData.posRef.z],
            angle: agentData.angle || 0,
            type: agentData.type,
            speed: agentData.speed,
            // 可以添加其他需要的字段
          });
        }
      }); // -- End agent loop --

      // 每帧结束后，统一调用一次回调
      if (updatedAgentsForCallback.length > 0) {
        onPositionUpdate(updatedAgentsForCallback);
      }

      // 请求下一帧
      animationFrameId.current = requestAnimationFrame(runAnimationFrame);
    };
    // --- End 动画循环核心逻辑 ---

    // 根据 updateInterval 选择循环方式
    if (updateInterval > 0) {
      // 使用 setInterval (不推荐，可能不平滑)
      console.warn("使用 setInterval 进行动画更新，可能不平滑");
      intervalId.current = setInterval(() => {
        runAnimationFrame(); // 手动调用帧逻辑
      }, updateInterval);
      isAnimationLoopRunning.current = true;
    } else {
      // 使用 requestAnimationFrame (推荐)
      animationFrameId.current = requestAnimationFrame(runAnimationFrame);
      isAnimationLoopRunning.current = true;
    }

    // Effect 清理函数
    return () => {
      console.log("清理 AgentController 动画循环 Effect");
      if (animationFrameId.current) {
        cancelAnimationFrame(animationFrameId.current);
        animationFrameId.current = null;
      }
      if (intervalId.current) {
        clearInterval(intervalId.current);
        intervalId.current = null;
      }
      // 杀死所有此组件创建的动画
      agentDataMapRef.current.forEach(agentData => {
        if (agentData.animation) {
          agentData.animation.kill();
          agentData.animation = null; // 清除引用
        }
      });
      isAnimationLoopRunning.current = false; // 标记循环已停止
    };

  }, [enabled, syncWithBackend, syncInterval, updateInterval, onPositionUpdate, boundary, type]); // 依赖项控制动画循环的启动/停止

  // 这是一个逻辑组件，不渲染任何UI
  return null;
};

export default AgentController;