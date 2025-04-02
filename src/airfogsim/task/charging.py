from airfogsim.core.task import Task
from airfogsim.task.proof.charging import ChargingProof
from typing import Dict

class ChargingTask(Task):
    """执行电池充电任务"""
    PROOF_CLASS = ChargingProof
    NECESSARY_METRICS = ['charging_rate']
    PRODUCED_STATES = ['battery_level']
    
    def __init__(self, env, agent, component_name, task_name,
                 workflow_id=None, proof_id=None, 
                 target_state=None, properties=None):
        """
        初始化充电任务
        
        Args:
            env: 仿真环境
            agent: 代理ID
            component_name: 组件名称
            task_name: 任务名称
            workflow_id: 工作流ID
            proof_id: 证明ID
            target_state: 目标状态，必须包含'battery_level'
            properties: 任务属性
        """
        # 确保目标状态和属性不为空
        target_state = target_state or {}
        properties = properties or {}
        
        # 调用父类初始化
        super().__init__(env, agent, component_name, task_name, 
                         workflow_id, proof_id, target_state, properties)
        
        # 任务特定属性
        self.target_battery_level = target_state.get('battery_level', 100.0)
        self.start_battery_level = properties.get('current_battery_level', 0.0)
        self.current_battery_level = self.start_battery_level
        self.charging_efficiency = properties.get('charging_efficiency', 0.95)  # 充电效率
    
    def estimate_remaining_time(self, performance_metrics: Dict) -> float:
        """估计完成任务所需的总时间"""
        charging_rate = performance_metrics.get('charging_rate', 0)
        if charging_rate <= 0:
            return float('inf')
        
        remaining_charge = self.target_battery_level - self.current_battery_level
        if remaining_charge <= 0:
            return 0
        
        return remaining_charge / (charging_rate * self.charging_efficiency)
    
    def _update_task_state(self, performance_metrics: Dict):
        """更新任务进度和内部状态"""
        charging_rate = performance_metrics.get('charging_rate', 0)
        elapsed_time = self.env.now - self.last_update_time
        charge_added = charging_rate * elapsed_time * self.charging_efficiency
        self.current_battery_level = min(100.0, self.current_battery_level + charge_added)
        
        total_charge_needed = self.target_battery_level - self.start_battery_level
        if total_charge_needed > 0:
            charge_completed = self.current_battery_level - self.start_battery_level
            self.progress = min(1.0, charge_completed / total_charge_needed)
        else:
            self.progress = 1.0
    
    def _get_task_specific_state_repr(self) -> Dict:
        """返回任务特定状态的表示"""
        return {
            'battery_level': self.current_battery_level
        }
    
    def _trigger_workflow_events(self):
        """触发工作流相关事件"""
        if self.proof and self.progress > 0:
            self.proof.update_battery_level(self.current_battery_level, self.agent_id)
            if self.env and self.workflow_id:
                agent = self.env.get_agent(self.agent_id)
                if agent:
                    agent.trigger_event('proof_updated', {
                        'proof_id': self.proof_id,
                        'data': {'battery_level': self.current_battery_level},
                        'workflow_id': self.workflow_id,
                        'time': self.env.now
                    })