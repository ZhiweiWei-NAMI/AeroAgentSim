from airfogsim.core.task import Task
from typing import Dict
from .proof.computation import ComputationProof

class ComputeTask(Task):
    """执行计算任务"""
    PROOF_CLASS = ComputationProof
    NECESSARY_METRICS = ['processing_speed']
    PRODUCED_STATES = ['computation_load']
    
    def __init__(self, env, agent, component_name, task_name,
                 workflow_id=None, proof_id=None, 
                 target_state=None, properties=None):
        """
        初始化计算任务
        
        Args:
            env: 仿真环境
            agent: 代理ID
            component_name: 组件名称
            task_name: 任务名称
            workflow_id: 工作流ID
            proof_id: 证明ID
            target_state: 目标状态
            properties: 任务属性
        """
        # 确保目标状态和属性不为空
        target_state = target_state or {}
        properties = properties or {}
        
        # 调用父类初始化
        super().__init__(env, agent, component_name, task_name, 
                         workflow_id, proof_id, target_state, properties)
        
        # 任务特定属性
        self.computation_size = properties.get('computation_size', 1000)
        self.computation_type = properties.get('computation_type', 'standard')
        self.result_callback = properties.get('result_callback')
        
        self.operations_processed = 0
        self.computation_result = None
        self.processing_progress = 0.0
        self.computation_load = 0.0
        self._proof_handover_workflow_class = None
    
    def estimate_total_time(self, performance_metrics: Dict) -> float:
        """估计完成任务所需的总时间"""
        # 获取当前处理速度
        processing_speed = performance_metrics.get('processing_speed', 0)
        
        # 避免除零错误
        if processing_speed <= 1e-6:
            return float('inf')
        
        # 剩余操作数/处理速度 = 预计时间
        remaining_operations = self.computation_size - self.operations_processed
        estimated_time = remaining_operations / processing_speed
        
        return max(0, estimated_time)
    
    def _update_task_state(self, elapsed_time: float, performance_metrics: Dict):
        """更新任务进度和内部状态"""
        # Ensure we have all needed metrics
        for metric in self.NECESSARY_METRICS:
            if metric not in performance_metrics:
                self.fail(f"缺少执行任务所需的指标: '{metric}'")
                return
        
        # 获取当前处理速度
        processing_speed = performance_metrics.get('processing_speed', 0)
        
        # 计算这段时间处理的操作数
        operations_done = processing_speed * elapsed_time
        self.operations_processed += operations_done
        
        # 更新进度
        if self.computation_size > 0:
            self.progress = min(1.0, self.operations_processed / self.computation_size)
            self.processing_progress = self.progress
        else:
            self.progress = 1.0
            self.processing_progress = 1.0
        
        # 更新计算负载 - 随着进度动态变化
        # 例如：模拟先增加后减少的计算负载
        if self.progress < 0.5:
            self.computation_load = self.progress * 2  # 0到1.0
        else:
            self.computation_load = 2.0 - (self.progress * 2)  # 1.0到0
        
        # 如果任务完成，生成结果
        if self.progress >= 1.0:
            self.computation_result = f"computation_{self.id}_result"
            # 调用回调函数（如果有）
            if self.result_callback:
                try:
                    self.result_callback(self.computation_result)
                except Exception as e:
                    print(f"时间 {self.env.now}: 计算结果回调错误: {e}")
    
    def _get_task_specific_state_repr(self) -> Dict:
        """返回任务特定状态的表示"""
        return {
            'computation_load': self.computation_load
        }
    
    def _trigger_workflow_events(self):
        """触发工作流相关事件"""
        # 计算任务可能不需要触发位置相关的事件
        # 但如果工作流需要跟踪计算进度，可以实现适当的事件
        
        # 如果任务完成，可以触发计算完成事件
        if self.progress >= 1.0 and self.workflow_id:
            agent = self.env.get_agent(self.agent_id)
            if agent:
                agent.trigger_event('computation_completed', {
                    'task_id': self.id,
                    'task_name': self.name,
                    'result': self.computation_result,
                    'workflow_id': self.workflow_id,
                    'time': self.env.now
                })