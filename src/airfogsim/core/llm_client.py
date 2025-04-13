"""
AirFogSim LLM 客户端模块

该模块提供了与大型语言模型（LLM）交互的功能，用于智能任务规划和决策。
主要功能包括：
1. LLM 客户端初始化和配置
2. 提示构建和发送
3. 响应解析和验证

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import json
import re
from typing import Dict, List, Optional

class LLMClient:
    """LLM 客户端类，用于与大型语言模型交互"""

    def __init__(self, env=None, api_key: Optional[str] = None, model: str = "gpt-4o"):
        """
        初始化 LLM 客户端

        Args:
            env: 环境实例，用于获取任务类
            api_key: API 密钥，如果为 None，则尝试从环境变量中获取
            model: 使用的模型名称，默认为 "gpt-4o"
        """
        self.model = model
        self.client = None
        self.env = env

        try:
            from openai import OpenAI
            self.client = OpenAI(api_key=api_key)
        except ImportError:
            print("警告: OpenAI 包未安装，LLM 功能将不可用")
        except Exception as e:
            print(f"初始化 OpenAI 客户端失败: {str(e)}")

    def is_available(self) -> bool:
        """
        检查 LLM 客户端是否可用

        Returns:
            bool: 如果客户端可用，则返回 True，否则返回 False
        """
        return self.client is not None

    def analyze_workflow(self, workflow, agent_state: Dict, available_components: List[str], env=None) -> List[Dict]:
        """
        分析工作流并生成任务

        Args:
            workflow: 工作流对象
            agent_state: 代理当前状态
            available_components: 可用组件列表
            env: 环境实例，用于获取任务类

        Returns:
            List[Dict]: 任务列表，每个任务是一个字典
        """
        if not self.is_available():
            return []

        # 保存环境实例供后续使用
        self.env = env

        # 构建提示，包含工作流状态机信息
        prompt = self._build_workflow_prompt(workflow, agent_state, available_components)

        try:
            # 使用 OpenAI 新版客户端 API
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a drone task planner assistant."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=1000
            )
            # 提取回复内容
            response_text = response.choices[0].message.content
            tasks = self._parse_response(response_text)

            # 确保每个任务都有工作流 ID
            for task in tasks:
                task['workflow_id'] = workflow.id

            return tasks
        except Exception as e:
            print(f"LLM 分析失败: {str(e)}")
            return []

    def _build_workflow_prompt(self, workflow, agent_state: Dict, available_components: List[str]) -> str:
        """
        构建工作流分析提示

        Args:
            workflow: 工作流对象
            agent_state: 代理当前状态
            available_components: 可用组件列表

        Returns:
            str: 提示字符串
        """
        # 获取可用的任务类
        available_task_classes = self._get_available_task_classes()

        # 构建提示
        prompt = f"""
            Suggest tasks directly without analysis:

            Workflow Name: {workflow.name}
            Current State: {agent_state}
            Current Workflow State: {workflow.status_machine.state}
            Current Workflow Details: {workflow.get_details()}
            Possible Next States: {[t[3] for t in workflow.status_machine._get_current_transitions()]}
            Available Task Classes: {available_task_classes}
            Available Components: {available_components}

            Return a JSON array of tasks following this format:
            [
            {{
                "component": "ComponentName",
                "task_class": "TaskClassName",
                "task_name": "Human readable task name",
                "workflow_id": "{workflow.id}",
                "target_state": {{"position": [x, y, z]}},  # Target drone state
                "properties": {{
                "key1": "value1",
                "key2": "value2"
                }}
            }}
            ]
            """
        return prompt

    def _get_available_task_classes(self) -> Dict[str, str]:
        """
        获取可用的任务类及其文档

        Returns:
            Dict[str, str]: 任务类名称到文档的映射
        """
        # 使用环境的task_manager获取所有注册的任务类
        task_classes = {}

        try:
            # 使用保存的环境实例
            if hasattr(self, 'env') and self.env and hasattr(self.env, 'task_manager'):
                # 获取所有注册的任务类
                for task_name, task_class in self.env.task_manager.task_classes.items():
                    # 获取任务类的文档
                    doc = task_class.__init__.__doc__ if hasattr(task_class, '__init__') and task_class.__init__.__doc__ else ""
                    task_classes[task_name] = doc
            else:
                # 如果没有环境实例，使用默认任务类
                from airfogsim.task.mobility import MoveToTask
                task_classes['MoveToTask'] = MoveToTask.__init__.__doc__
        except Exception as e:
            print(f"获取任务类失败: {str(e)}")
            # 使用默认任务类
            from airfogsim.task.mobility import MoveToTask
            task_classes['MoveToTask'] = MoveToTask.__init__.__doc__

        return task_classes

    def _parse_response(self, response: str) -> List[Dict]:
        """
        解析 LLM 响应并转换为任务列表

        Args:
            response: LLM 响应文本

        Returns:
            List[Dict]: 任务列表，每个任务是一个字典
        """
        tasks = []

        try:
            # 提取可能的 JSON 部分
            json_match = re.search(r'```json\n(.*?)\n```', response, re.DOTALL)
            if json_match:
                json_content = json_match.group(1)
            else:
                json_content = response

            task_data = json.loads(json_content)
            if isinstance(task_data, list):
                for task in task_data:
                    if self._validate_task_format(task):
                        tasks.append(task)
        except Exception as e:
            print(f"解析 LLM 响应失败: {str(e)}")

        return tasks

    def _validate_task_format(self, task: Dict) -> bool:
        """
        验证任务格式是否正确

        Args:
            task: 任务字典

        Returns:
            bool: 如果任务格式正确，则返回 True，否则返回 False
        """
        required_fields = ['component', 'task_name', 'task_class', 'target_state', 'properties']
        return all(field in task for field in required_fields)
