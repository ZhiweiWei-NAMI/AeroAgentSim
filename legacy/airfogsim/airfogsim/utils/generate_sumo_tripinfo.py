import traci
from tqdm import tqdm


def generateSUMOTripinfo(config, output_sumo_tripinfo_xml):
    traci.start(["sumo", "--no-step-log", "--no-warnings", "--log", "sumo.log", "-c", config['sumo_config'], '--tripinfo-output', output_sumo_tripinfo_xml],
                port=config['sumo_port'])
    sim_duration = 10000  # 仿真运行的最大步数
    step = 0
    for i in tqdm(range(sim_duration), total=sim_duration, desc='Running SUMO'):
        traci.simulationStep()  # 让仿真走一步
        step += 1
if __name__ == '__main__':
    # 获取整个项目当前的路径
    import sys
    import os

    # 获取 sys.path 中的第一个路径，通常是根目录
    dir_path = os.path.abspath(sys.path[0])
    # 向上一个目录
    dir_path = os.path.dirname(dir_path)
    dir_path = os.path.dirname(dir_path)
    print(dir_path)
    config = {
        'sumo_config': os.path.join(dir_path, 'sumo_wujiaochang', 'osm.sumocfg'),
        'sumo_port': 8813
    }
    output_sumo_tripinfo_xml = os.path.join(dir_path, 'sumo_wujiaochang', 'tripinfo.xml')
    generateSUMOTripinfo(config, output_sumo_tripinfo_xml)