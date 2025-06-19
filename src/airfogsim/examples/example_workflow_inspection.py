from airfogsim.core.environment import Environment
from airfogsim.agent import DroneAgent
from airfogsim.component import MoveToComponent, ChargingComponent
from airfogsim.workflow.inspection import create_inspection_workflow
from openai import OpenAI  # OpenAI新版客户端
# 在文件顶部导入模块
from airfogsim.workflow.charging import create_charging_workflow
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

# 在创建无人机后，添加充电工作流
def setup_charging_workflow(env, drone, battery_threshold=50):
    """为无人机设置充电工作流"""
    # 使用LandingManager查找最近的充电站
    position3d = drone.get_state('position')
    nearest_charging_station = env.landing_manager.find_nearest_landing_spot(
        x=position3d[0],
        y=position3d[1],
        require_charging=True
    )
    
    if nearest_charging_station:
        charging_station_location = nearest_charging_station.location
        charging_workflow = create_charging_workflow(
            env=env,
            agent=drone,
            charging_station=charging_station_location,
            battery_threshold=battery_threshold,
            target_charge_level=90
        )
        logger.info(f"为无人机 {drone.id} 创建充电工作流，充电站位置: {charging_station_location}")
        return charging_workflow
    else:
        logger.info(f"未找到可用的充电站为无人机 {drone.id}")
        return None


# 创建环境
env = Environment(visual_interval=10)

# 创建和注册着陆区资源
# 创建起飞/降落点
home_landing_id = env.landing_manager.create_landing_spot(
    location=(10, 10, 0),
    radius=15.0,
    max_capacity=2,
    has_charging=True,
    has_data_transfer=True,
    attributes={'name': '基地', 'charging_power': 200.0, 'data_transfer_rate': 50.0}
)

# 创建目的地着陆点
dest_landing_id = env.landing_manager.create_landing_spot(
    location=(800, 800, 0),
    radius=10.0,
    max_capacity=1,
    has_charging=False,
    has_data_transfer=True,
    attributes={'name': '目的地', 'data_transfer_rate': 20.0}
)

# 创建中途休息站
rest_landing_id = env.landing_manager.create_landing_spot(
    location=(400, 400, 0),
    radius=8.0,
    max_capacity=3,
    has_charging=True,
    has_data_transfer=False,
    attributes={'name': '休息站', 'charging_power': 150.0}
)

# 创建OpenAI客户端（新版API）
client = OpenAI(
    api_key="sk-api",  # 您的API密钥
    base_url="https://yunwu.ai/v1"  # 替换为您的API端点
)

# 创建无人机代理，传入OpenAI客户端
drone = env.create_agent(
    DroneAgent, 
    "drone1", 
    properties={
        'position':(10, 10, 0),  # 从home_landing位置起飞
        'battery_level':50,
        'llm_client':None
    }
)

# 创建MoveToComponent和CPUComponent
move_component = MoveToComponent(env, drone)
charging_component = ChargingComponent(env, drone)

# 添加组件到无人机
drone.add_component(move_component)
drone.add_component(charging_component)

# 为无人机设置充电工作流（由触发器自动启动）
charging_workflow = setup_charging_workflow(env, drone, battery_threshold=10)

# 创建巡检工作流，定义飞行路径
# 从起点(10,10,0)起飞，经过目的地(800,800,0)，返回起点
waypoints = [
    (10, 10, 100),    # 从起飞点升空
    (400, 400, 150),  # 飞到休息站上方
    (800, 800, 150),  # 飞到目的地上方
    (800, 800, 50),   # 降低高度准备降落
    (800, 800, 0),    # 降落到目的地
    (800, 800, 100),  # 从目的地起飞
    (400, 400, 100),  # 返程经过休息站
    (10, 10, 100),    # 返回起飞点上方
    (10, 10, 0)       # 降落回起飞点
]

workflow = create_inspection_workflow(env, drone, waypoints)

# 不再需要手动分配资源，组件会在任务执行过程中自动处理资源分配
# 这是因为我们重构了Component基类，使其在_execute_task_wrapper方法中
# 调用子类的_allocate_task_resources和_release_task_resources方法

# 运行模拟
env.run(until=1000)

# 不再需要手动释放资源，组件会在任务完成时自动释放