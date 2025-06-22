"""
多任务合约示例

该示例展示了如何创建和执行包含多个任务的合约。
合约工作流会自动管理多个任务的执行顺序和状态。
"""

from airfogsim.core.environment import Environment
from airfogsim.agent.drone import DroneAgent
from airfogsim.agent.terminal import TerminalAgent
from airfogsim.component import MoveToComponent
from airfogsim.component.img_sensor import ImageSensingComponent
import uuid
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

def run_multi_task_contract_example():
    # 创建环境
    env = Environment(visual_interval=10)
    
    # 创建代理
    terminal = TerminalAgent(env, agent_name="地面站", properties={'position': (0, 0, 0)})
    drone = DroneAgent(env, agent_name="无人机", properties={
        'position': (0, 0, 0),
        'battery_level': 100
    })
    
    # 注册代理
    env.register_agent(terminal)
    env.register_agent(drone)
    
    # 添加组件到无人机
    move_component = MoveToComponent(env, drone, name="移动组件")
    sensing_component = ImageSensingComponent(env, drone, name="感知组件")
    drone.add_component(move_component)
    drone.add_component(sensing_component)
    
    # 为代理添加初始余额
    contract_manager = env.contract_manager
    contract_manager.set_agent_balance(terminal.id, 1000)
    contract_manager.set_agent_balance(drone.id, 500)
    
    # 定义多任务合约
    task_info = {
        "tasks": [
            {
                "id": f"task_takeoff_{uuid.uuid4()}",
                "component": "移动组件",
                "task_class": "MoveToTask",
                "task_name": "起飞任务",
                "workflow_id": None,
                "target_state": {"position": (0, 0, 50)},
                "properties": {
                    "target_position": (0, 0, 50),
                    "speed": 5
                }
            },
            {
                "id": f"task_survey_{uuid.uuid4()}",
                "component": "感知组件",
                "task_class": "FileCollectTask",
                "task_name": "区域扫描",
                "workflow_id": None,
                "target_state": {},
                "properties": {
                    "file_name": "scan_result",
                    "file_type": "image",
                    "file_size": 2048,
                    "content_type": "image",
                    "location": (150, 150, 50),
                    "sensing_difficulty": 1.2
                }
            },
            {
                "id": f"task_return_base_{uuid.uuid4()}",
                "component": "移动组件",
                "task_class": "MoveToTask",
                "task_name": "返回基地",
                "workflow_id": None,
                "target_state": {"position": (0, 0, 0)},
                "properties": {
                    "target_position": (0, 0, 0),
                    "speed": 7
                }
            }
        ]
    }
    
    # 创建合约
    contract_id = contract_manager.create_contract(
        issuer_agent_id=terminal.id,
        task_info=task_info,
        reward=100,
        penalty=50,
        deadline=1000,  # 假设单位为秒
        appointed_agent_ids=[drone.id],
        description="执行多任务巡检合约"
    )
    
    logger.info(f"创建了合约 {contract_id}")
    
    # 接受合约
    def accept_contract():
        yield env.timeout(10)  # 延迟10秒接受合约
        success = contract_manager.accept_contract(contract_id, drone.id)
        logger.info(f"无人机接受合约: {'成功' if success else '失败'}")
    
    env.process(accept_contract())
    
    # 运行仿真
    env.run(until=1000)
    
    # 打印最终结果
    logger.info("\n=== 合约执行结果 ===")
    contract = contract_manager.get_contract(contract_id)
    logger.info(f"合约状态: {contract['status']}")
    logger.info(f"完成时间: {contract['completion_time']}")
    logger.info(f"地面站余额: {contract_manager.get_agent_balance(terminal.id)}")
    logger.info(f"无人机余额: {contract_manager.get_agent_balance(drone.id)}")

if __name__ == "__main__":
    run_multi_task_contract_example()