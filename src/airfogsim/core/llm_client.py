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
from typing import Dict, List, Optional, TYPE_CHECKING
# Import find_compatible_tasks function at runtime to avoid circular imports
from airfogsim.utils.logging_config import get_logger

# 获取logger
logger = get_logger(__name__)

if TYPE_CHECKING:
    from airfogsim.core.agent import Agent

class LLMClient:
    """LLM 客户端类，用于与大型语言模型交互"""

    def __init__(self, env, api_key: Optional[str] = None, model: str = "gpt-4o"):
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
            logger.warning("警告: OpenAI 包未安装，LLM 功能将不可用")
        except Exception as e:
            logger.error(f"初始化 OpenAI 客户端失败: {str(e)}")

    def is_available(self) -> bool:
        """
        检查 LLM 客户端是否可用

        Returns:
            bool: 如果客户端可用，则返回 True，否则返回 False
        """
        return self.client is not None

    def analyze_agent_tasks(self, agent: 'Agent') -> List[Dict]:
        """
        分析代理，生成需要执行的任务和等待的任务

        Args:
            agent: 代理对象

        Returns:
            to_execute_tasks 和 queuing_tasks
            List[Dict]: 需执行任务列表，每个任务是一个字典
            List[Dict]: 等待任务列表，每个任务是一个字典
        """
        if not self.is_available():
            return []

        # 在运行时导入find_compatible_tasks函数，避免循环导入
        from airfogsim.helper.class_checker import find_compatible_tasks

        workflows = agent.get_active_workflows()
        agent_details = agent.get_details()
        possible_tasks = []
        for workflow in workflows:
            tmp_tasks = find_compatible_tasks(self.env, agent, workflow)
            possible_tasks.extend(tmp_tasks)

        # 构建提示，包含工作流状态机信息
        prompt = self._build_workflow_prompt(workflows, agent_details, possible_tasks)

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
            logger.error(f"LLM 分析失败: {str(e)}")
            return []

    def _build_workflow_prompt(self, workflows, agent_details: Dict, possible_tasks: List) -> str:
        """
        构建工作流分析提示

        Args:
            workflows: 工作流列表
            agent_details: 代理当前详细信息
            possible_tasks: 可能的任务列表，每个元素是(task_class, compatibility_score)元组

        Returns:
            str: 提示字符串
        """
        # 获取可用的任务类
        available_task_classes = self._get_available_task_classes()

        # 从agent_details中提取组件信息
        available_components = agent_details.get('components', [])

        # 格式化可能的任务信息
        formatted_tasks = []
        for task_class, score in possible_tasks:
            task_info = {
                'name': task_class.__name__,
                'compatibility_score': f"{score:.2f}",
                'necessary_metrics': list(getattr(task_class, 'NECESSARY_METRICS', [])),
                'produced_states': list(getattr(task_class, 'PRODUCED_STATES', [])),
                'description': task_class.__doc__ or f"{task_class.__name__} 任务"
            }
            formatted_tasks.append(task_info)

        # 构建提示
        prompt = f"""
            Suggest tasks directly without analysis:

            Agent Details: {json.dumps(agent_details, ensure_ascii=False)}

            Workflows: {[w.id for w in workflows]}
            """

        # 为每个工作流添加详细信息
        for workflow in workflows:
            prompt += f"""
            Workflow ID: {workflow.id}
            Workflow Name: {workflow.name if hasattr(workflow, 'name') else 'Unknown'}
            Current Workflow State: {workflow.status_machine.state if hasattr(workflow, 'status_machine') and hasattr(workflow.status_machine, 'state') else 'Unknown'}
            Current Workflow Details: {workflow.get_details() if hasattr(workflow, 'get_details') else {}}
            Possible Next States: {[t[3] for t in workflow.status_machine._get_current_transitions()] if hasattr(workflow, 'status_machine') and hasattr(workflow.status_machine, '_get_current_transitions') else []}
            """

        prompt += f"""
            Available Task Classes: {json.dumps(available_task_classes, ensure_ascii=False)}
            Available Components: {json.dumps(available_components, ensure_ascii=False)}
            Compatible Tasks: {json.dumps(formatted_tasks, ensure_ascii=False)}

            Return a JSON array of tasks following this format:
            [
            {{
                "component": "ComponentName",
                "task_class": "TaskClassName",
                "task_name": "Human readable task name",
                "workflow_id": "workflow-id",
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
            logger.error(f"获取任务类失败: {str(e)}")
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
            logger.error(f"解析 LLM 响应失败: {str(e)}")

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


if __name__ == "__main__":
    """
    LLM客户端测试代码
    测试find_compatible_tasks的返回格式和_build_workflow_prompt方法
    """
    import os
    import sys
    from typing import List
    from unittest.mock import MagicMock

    # 添加项目根目录到系统路径
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

    # 模拟环境和代理
    class MockTask:
        NECESSARY_METRICS = ['position', 'battery']
        PRODUCED_STATES = ['position']

        def __init__(self, **_):
            self.__doc__ = "Mock task for testing"

    class MockWorkflow:
        def __init__(self, workflow_id: str, name: str):
            self.id = workflow_id
            self.name = name
            self.status_machine = MagicMock()
            self.status_machine.state = "IDLE"
            self.status_machine._get_current_transitions = MagicMock(return_value=[(None, None, None, "RUNNING")])

        def get_details(self):
            return {"status": "ready", "progress": 0}

    class MockAgent:
        def __init__(self, agent_id: str):
            self.id = agent_id
            self._workflows = []

        def get_active_workflows(self):
            return self._workflows

        def add_workflow(self, workflow):
            self._workflows.append(workflow)

        def get_details(self):
            return {
                "id": self.id,
                "type": "drone",
                "state": {
                    "position": [0, 0, 10],
                    "battery": 95
                },
                "components": ["camera", "gps", "battery"]
            }

        def get_component_names(self):
            return ["camera", "gps", "battery"]

    class MockEnvironment:
        def __init__(self):
            self.task_manager = MagicMock()
            self.task_manager.task_classes = {"MockTask": MockTask}
            self.now = 0

    # 模拟 find_compatible_tasks 函数
    def mock_find_compatible_tasks(*_, **__):
        """模拟函数，忽略所有参数，返回固定结果"""
        return [(MockTask, 0.85)]

    # 在测试中使用模拟函数，不需要导入原始模块

    # 创建测试环境
    env = MockEnvironment()
    agent = MockAgent("drone-001")
    workflow = MockWorkflow("workflow-001", "Test Workflow")
    agent.add_workflow(workflow)

    # 创建LLM客户端
    llm_client = LLMClient(env)

    # 测试_build_workflow_prompt方法
    workflows = agent.get_active_workflows()
    agent_details = agent.get_details()
    possible_tasks = mock_find_compatible_tasks(env, agent, workflow)

    prompt = llm_client._build_workflow_prompt(workflows, agent_details, possible_tasks)
    logger.info("\n=== Generated Prompt ===\n")
    logger.info(prompt)
    logger.info("\n=== End of Prompt ===\n")

    # 测试解析响应
    mock_response = """
    ```json
    [
      {
        "component": "gps",
        "task_class": "MockTask",
        "task_name": "Navigate to position",
        "workflow_id": "workflow-001",
        "target_state": {"position": [10, 20, 30]},
        "properties": {
          "speed": 5,
          "altitude": 30
        }
      }
    ]
    ```
    """

    tasks = llm_client._parse_response(mock_response)
    logger.info("\n=== Parsed Tasks ===\n")
    for task in tasks:
        logger.info(json.dumps(task, indent=2))
    logger.info("\n=== End of Tasks ===\n")
