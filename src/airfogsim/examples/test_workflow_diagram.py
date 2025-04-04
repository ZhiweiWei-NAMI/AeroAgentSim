
from airfogsim.workflow.inspection import create_inspection_workflow
from airfogsim.core.environment import Environment
from airfogsim.agent import DroneAgent

# 创建环境
env = Environment(visual_interval=10)


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

# 生成UML活动图
plantuml_code = workflow.to_uml_activity_diagram()
# 可导出到.puml文件或直接用PlantUML渲染
print(plantuml_code)
# 生成Mermaid格式图表
mermaid_code = workflow.to_mermaid_diagram()
# 可在支持Mermaid的Markdown编辑器中使用
print(mermaid_code)