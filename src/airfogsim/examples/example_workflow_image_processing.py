"""
AirFogSim环境图像感知处理示例

该示例展示了如何创建和运行环境图像感知处理工作流，包括感知和计算两个阶段。
"""

from airfogsim.core.environment import Environment
from airfogsim.agent.drone import DroneAgent
from airfogsim.component.mobility import MoveToComponent
from airfogsim.component.computation import ComputationComponent
from airfogsim.component.img_sensor import ImageSensingComponent
from airfogsim.manager.file import FileManager
from airfogsim.manager.airspace import AirspaceManager
from airfogsim.workflow.image_processing import create_image_processing_workflow
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

def run_image_processing_simulation():
    """运行环境图像感知处理仿真"""
    # 创建仿真环境
    env = Environment()
    
    # 创建管理器
    env.airspace_manager = AirspaceManager(env)
    env.file_manager = FileManager(env)
    
    # 创建无人机
    drone = DroneAgent(
        env=env,
        agent_name="drone_1",
        properties={
            'position': (0, 0, 0),
            'battery_capacity': 5000,  # mAh
            'current_battery': 5000,   # mAh
            'max_speed': 10,           # m/s
            'weight': 1.5              # kg
        }
    )
    
    # 添加组件
    drone.add_component(
        MoveToComponent(
            env=env,
            agent=drone,
            properties={
                'speed': 5.0,  # m/s
                'acceleration': 2.0,  # m/s^2
                'energy_consumption_rate': 10.0  # mAh/s
            }
        )
    )
    
    drone.add_component(
        ComputationComponent(
            env=env,
            agent=drone,
            properties={
                'processing_power': 1000,  # 处理能力单位
                'computation_efficiency': {
                    'image': 0.8,  # 图像处理效率
                    'text': 1.0,
                    'sensor_data': 0.9
                },
                'energy_consumption_rate': 5.0  # mAh/s
            }
        )
    )
    
    drone.add_component(
        ImageSensingComponent(
            env=env,
            agent=drone,
            properties={
                'sensing_capability': 500,  # 感知能力单位
                'sensing_efficiency': {
                    'image': 0.7,  # 图像感知效率
                    'text': 1.0,
                    'sensor_data': 0.9
                },
                'energy_consumption_rate': 3.0  # mAh/s
            }
        )
    )
    
    # 定义感知位置
    sensing_locations = [
        (10, 10, 5),   # 位置1
        (20, 20, 5),   # 位置2
        (30, 30, 5)    # 位置3
    ]
    
    # 创建环境图像感知处理工作流
    workflow = create_image_processing_workflow(
        env=env,
        agent=drone,
        sensing_locations=sensing_locations,
        image_resolution='1920x1080',
        image_format='jpeg'
    )
    
    # 启动仿真
    logger.info("开始环境图像感知处理仿真...")
    env.run(until=1000)
    logger.info("仿真完成!")
    
    # 输出结果
    logger.info("\n工作流执行结果:")
    logger.info(f"工作流状态: {workflow.status}")
    logger.info(f"感知的文件: {workflow.collected_file_ids}")
    logger.info(f"处理后的文件: {workflow.processed_file_ids}")
    
    # 输出文件详情
    logger.info("\n文件详情:")
    for file_id in workflow.collected_file_ids:
        file_info = env.file_manager.get_file(file_id)
        logger.info(f"感知文件 {file_id}: {file_info.get('name')} - 类型: {file_info.get('type')}")
    
    for file_id in workflow.processed_file_ids:
        file_info = env.file_manager.get_file(file_id)
        logger.info(f"处理文件 {file_id}: {file_info.get('name')} - 类型: {file_info.get('type')}")

if __name__ == "__main__":
    run_image_processing_simulation()