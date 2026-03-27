#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM任务调度器

该模块提供基于LLM的智能任务规划和调度功能，可以作为Agent的可嵌入接口使用。
"""

import json
from typing import List, Dict, Any, Optional
from aeroagentsim.utils.logging_config import get_logger
from aeroagentsim.core.task import Task

logger = get_logger(__name__)


class LLMScheduler:
    """
    基于LLM的任务调度器
    
    该类提供智能任务规划功能，可以分析当前环境状态、代理状态和工作流信息，
    然后通过LLM生成合适的任务建议。
    """
    
    def __init__(self, llm_client=None, config: Optional[Dict] = None):
        """
        初始化LLM调度器
        
        Args:
            llm_client: LLM客户端实例
            config: 调度器配置
        """
        self.llm_client = llm_client
        self.config = config or {}
        
        # 默认配置
        self.max_suggestions = self.config.get('max_suggestions', 3)
        self.planning_interval = self.config.get('planning_interval', 10)  # 秒
        self.context_window = self.config.get('context_window', 100)  # 考虑的历史事件数量
        
        # 上次规划时间
        self.last_planning_time = 0
        
        logger.info("LLM调度器已初始化")
    
    def plan_tasks(self, agent) -> List[Task]:
        """
        为指定代理规划任务
        
        Args:
            agent: 目标代理实例
            
        Returns:
            List[Task]: 建议的任务列表
        """
        try:
            # 检查是否需要重新规划
            if not self._should_replan(agent):
                return []
            
            # 收集上下文信息
            context = self._collect_context(agent)
            
            # 如果没有LLM客户端，使用基础规划逻辑
            if not self.llm_client:
                return self._basic_planning(agent, context)
            
            # 使用LLM进行规划
            return self._llm_planning(agent, context)
            
        except Exception as e:
            logger.error(f"LLM调度器规划任务失败: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return []
    
    def _should_replan(self, agent) -> bool:
        """
        判断是否需要重新规划
        
        Args:
            agent: 代理实例
            
        Returns:
            bool: 是否需要重新规划
        """
        current_time = agent.env.now
        
        # 检查时间间隔
        if current_time - self.last_planning_time < self.planning_interval:
            return False
        
        # 检查代理状态变化
        if self._has_significant_state_change(agent):
            return True
        
        # 检查任务队列状态
        if len(agent.task_queue) == 0:
            return True
        
        return False
    
    def _has_significant_state_change(self, agent) -> bool:
        """
        检查代理是否有重要状态变化
        
        Args:
            agent: 代理实例
            
        Returns:
            bool: 是否有重要状态变化
        """
        # 这里可以根据具体需求实现状态变化检测逻辑
        # 例如：位置变化、资源变化、新的工作流分配等
        return True  # 简化实现，总是返回True
    
    def _collect_context(self, agent) -> Dict[str, Any]:
        """
        收集规划所需的上下文信息
        
        Args:
            agent: 代理实例
            
        Returns:
            Dict[str, Any]: 上下文信息
        """
        context = {
            'agent_id': agent.id,
            'agent_name': agent.name,
            'current_time': agent.env.now,
            'agent_state': dict(agent.state),
            'task_queue_size': len(agent.task_queue),
            'active_tasks': len(agent.managed_tasks),
            'components': list(agent.components.keys()),
            'possessing_objects': list(agent.possessing_objects.keys())
        }
        
        # 添加工作流信息
        try:
            active_workflows = agent.get_active_workflows()
            context['active_workflows'] = [
                {
                    'id': wf.id,
                    'name': getattr(wf, 'name', 'Unknown'),
                    'status': getattr(wf, 'status', 'Unknown'),
                    'progress': getattr(wf, 'progress', 0)
                }
                for wf in active_workflows
            ]
        except Exception as e:
            logger.warning(f"收集工作流信息失败: {str(e)}")
            context['active_workflows'] = []
        
        # 添加环境信息
        try:
            context['environment'] = {
                'current_time': agent.env.now,
                'total_agents': len(getattr(agent.env, 'agents', [])),
                'active_contracts': len(getattr(agent.env, 'contract_manager', {}).get('contracts', {}))
            }
        except Exception as e:
            logger.warning(f"收集环境信息失败: {str(e)}")
            context['environment'] = {}
        
        return context
    
    def _basic_planning(self, agent, context: Dict[str, Any]) -> List[Task]:
        """
        基础规划逻辑（不使用LLM）
        
        Args:
            agent: 代理实例
            context: 上下文信息
            
        Returns:
            List[Task]: 建议的任务列表
        """
        suggested_tasks = []
        
        # 简单的基于规则的规划
        # 1. 如果任务队列为空，尝试从工作流获取任务
        if context['task_queue_size'] == 0:
            try:
                workflow_tasks = agent._process_workflow_tasks()
                if workflow_tasks:
                    logger.info(f"基础规划器为代理 {agent.id} 建议 {len(workflow_tasks)} 个工作流任务")
            except Exception as e:
                logger.warning(f"获取工作流任务失败: {str(e)}")
        
        # 2. 检查是否需要维护任务
        if self._needs_maintenance(agent, context):
            # 这里可以创建维护任务
            logger.info(f"基础规划器检测到代理 {agent.id} 需要维护")
        
        self.last_planning_time = agent.env.now
        return suggested_tasks
    
    def _llm_planning(self, agent, context: Dict[str, Any]) -> List[Task]:
        """
        使用LLM进行任务规划
        
        Args:
            agent: 代理实例
            context: 上下文信息
            
        Returns:
            List[Task]: 建议的任务列表
        """
        try:
            # 构建LLM提示
            prompt = self._build_planning_prompt(context)
            
            # 调用LLM
            response = self.llm_client.generate_response(prompt)
            
            # 解析LLM响应
            suggested_tasks = self._parse_llm_response(agent, response)
            
            self.last_planning_time = agent.env.now
            logger.info(f"LLM规划器为代理 {agent.id} 建议 {len(suggested_tasks)} 个任务")
            
            return suggested_tasks
            
        except Exception as e:
            logger.error(f"LLM规划失败: {str(e)}")
            # 回退到基础规划
            return self._basic_planning(agent, context)
    
    def _build_planning_prompt(self, context: Dict[str, Any]) -> str:
        """
        构建LLM规划提示
        
        Args:
            context: 上下文信息
            
        Returns:
            str: LLM提示文本
        """
        prompt = f"""
你是一个智能任务规划器，需要为无人机代理规划合适的任务。

当前代理信息：
- 代理ID: {context['agent_id']}
- 代理名称: {context['agent_name']}
- 当前时间: {context['current_time']}
- 任务队列大小: {context['task_queue_size']}
- 活跃任务数: {context['active_tasks']}
- 可用组件: {', '.join(context['components'])}
- 拥有对象: {', '.join(context['possessing_objects'])}

代理状态：
{json.dumps(context['agent_state'], indent=2, ensure_ascii=False)}

活跃工作流：
{json.dumps(context['active_workflows'], indent=2, ensure_ascii=False)}

环境信息：
{json.dumps(context['environment'], indent=2, ensure_ascii=False)}

请基于以上信息，为该代理规划最多{self.max_suggestions}个合适的任务。

请以JSON格式返回任务建议，格式如下：
{{
  "tasks": [
    {{
      "task_name": "任务名称",
      "task_class": "任务类名",
      "component_name": "组件名称",
      "priority": "high/medium/low",
      "properties": {{}},
      "reasoning": "选择此任务的原因"
    }}
  ]
}}
"""
        return prompt
    
    def _parse_llm_response(self, agent, response: str) -> List[Task]:
        """
        解析LLM响应并创建任务
        
        Args:
            agent: 代理实例
            response: LLM响应文本
            
        Returns:
            List[Task]: 解析出的任务列表
        """
        try:
            # 尝试解析JSON响应
            data = json.loads(response)
            tasks = data.get('tasks', [])
            
            suggested_tasks = []
            for task_info in tasks[:self.max_suggestions]:
                try:
                    # 这里需要根据实际的任务创建逻辑来实现
                    # 由于任务创建比较复杂，这里只是记录建议
                    logger.info(f"LLM建议任务: {task_info['task_name']} - {task_info.get('reasoning', '')}")
                    
                    # 实际实现中，这里应该创建具体的Task实例
                    # task = self._create_task_from_suggestion(agent, task_info)
                    # suggested_tasks.append(task)
                    
                except Exception as e:
                    logger.warning(f"解析任务建议失败: {str(e)}")
                    continue
            
            return suggested_tasks
            
        except json.JSONDecodeError as e:
            logger.error(f"解析LLM响应JSON失败: {str(e)}")
            return []
    
    def _needs_maintenance(self, agent, context: Dict[str, Any]) -> bool:
        """
        检查代理是否需要维护
        
        Args:
            agent: 代理实例
            context: 上下文信息
            
        Returns:
            bool: 是否需要维护
        """
        # 这里可以实现具体的维护检查逻辑
        # 例如：电池电量、组件状态、位置等
        return False
    
    def set_config(self, config: Dict[str, Any]):
        """
        更新调度器配置
        
        Args:
            config: 新的配置字典
        """
        self.config.update(config)
        self.max_suggestions = self.config.get('max_suggestions', 3)
        self.planning_interval = self.config.get('planning_interval', 10)
        self.context_window = self.config.get('context_window', 100)
        
        logger.info(f"LLM调度器配置已更新: {config}")


def create_llm_scheduler(agent, llm_client=None, config: Optional[Dict] = None) -> LLMScheduler:
    """
    创建并配置LLM调度器的便捷函数
    
    Args:
        agent: 目标代理
        llm_client: LLM客户端实例
        config: 调度器配置
        
    Returns:
        LLMScheduler: 配置好的调度器实例
    """
    scheduler = LLMScheduler(llm_client, config)
    
    # 设置回调函数
    agent.set_task_planner_callback(scheduler.plan_tasks)
    
    logger.info(f"为代理 {agent.id} 创建并配置了LLM调度器")
    return scheduler