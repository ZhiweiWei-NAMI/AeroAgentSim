# Paper: AirFogSim: A Light-Weight and Modular Simulator for UAV-Integrated Vehicular Fog Computing
## Abstract:
Vehicular Fog Computing (VFC) is significantly enhancing the efficiency, safety, and computational capabilities of Intelligent Transportation Systems (ITS), and the integration of Unmanned Aerial Vehicles (UAVs) further elevates these advantages by incorporating flexible and auxiliary services. This evolving UAV-integrated VFC paradigm opens new doors while presenting unique complexities within the cooperative computation framework. Foremost among the challenges, modeling the intricate dynamics of aerial-ground interactive computing networks is a significant endeavor, and the absence of a comprehensive and flexible simulation platform may impede the exploration of this field. Inspired by the pressing need for a versatile tool, this paper provides a lightweight and modular aerial-ground collaborative simulation platform, termed AirFogSim. We present the design and implementation of AirFogSim, and demonstrate its versatility with five key missions in the domain of UAV-integrated VFC. A multifaceted use case is carried out to validate AirFogSim’s effectiveness, encompassing several integral aspects of the proposed AirFogSim, including UAV trajectory, task offloading, resource allocation, and blockchain. In general, AirFogSim is envisioned to set a new precedent in the UAV-integrated VFC simulation, bridge the gap between theoretical design and practical validation, and pave the way for future intelligent transportation domains. 

![image](https://github.com/ZhiweiWei-NAMI/AirFogSim/assets/153070550/0e28ce03-8eed-40e7-8f9d-a85e067df575)

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## How to use Real Data?
<!-- 在config.yaml中sumo-export_tripinfo设置为True，设置tripinfo_output，可以在指定位置输出tripinfo_output.xml文件 -->
First, you need to set the `sumo-export_tripinfo` to `True` in `config.yaml`, and set the `tripinfo_output` to the path you want to save the `tripinfo_output.xml` file. The code is like:
```yaml
sumo:
  sumo_config: "./sumo_wujiaochang/osm.sumocfg"
  sumo_osm: "./sumo_wujiaochang/osm_bbox.osm.xml"
  sumo_net: "./sumo_wujiaochang/osm.net.xml"
  sumo_port: 8813
  export_tripinfo: True # 如果true，则导出tripinfo.xml文件（很大）
  tripinfo_output: "./sumo_wujiaochang/tripinfo.xml"
```
Then, you can run the simulator to generate the `tripinfo_output.xml` file. After that, you should transform the `tripinfo_output.xml` file to the `tripinfo_output.csv` file. The code is like:
```
(airfogsim) (base) weizhiwei:~/data/airfogsim_code/$ python airfogsim/utils/xml2csv.py ./sumo_wujiaochang/tripinfo.xml
```
This will generate the `tripinfo_output.csv` file in the same directory. Then, you can use the real data in the simulator via yaml settings. The code is like:
```yaml
traffic:
  traffic_mode: "real" # "real" or "SUMO"
  tripinfo: "./sumo_wujiaochang/tripinfo.csv" # The path to the SUMO tripinfo file
```


## Setup:
1. Install [SUMO](https://sourceforge.net/projects/sumo/files/sumo/) (tested version is 1.15.0), and set the Environment variable. Once suceed, enter `sumo` in command line as:
```
(airfogsim) (base) weizhiwei:~/data/airfogsim_code/$ sumo
```
there should be:
```
Eclipse SUMO sumo Version 1.15.0
 Build features: Linux-5.4.0-131-generic x86_64 GNU 9.4.0 Release FMI Proj GUI Intl SWIG GDAL GL2PS Eigen
 Copyright (C) 2001-2022 German Aerospace Center (DLR) and others; https://sumo.dlr.de
 License EPL-2.0: Eclipse Public License Version 2 <https://eclipse.org/legal/epl-v20.html>
 Use --help to get the list of options.
```
2. Create the simulation environment (suggested to use `conda` for virtual environment) according to `requirements.txt`:
```
pip install -r requirements.txt
```
3. Run `example01_offloading_example.py`. Here is the main code:
```python
# 1. Load the configuration file
config_path = sys.argv[1] if len(sys.argv) > 1 else 'config.yaml'
config = load_config(config_path)

# 2. Create the environment
env = AirFogSimEnv(config, interactive_mode='graphic')
# env = AirFogSimEnv(config, interactive_mode=None)

# 3. Get algorithm module
algorithm_module = BaseAlgorithmModule()
algorithm_module.initialize(env)
accumulated_reward = 0
while not env.isDone():
    algorithm_module.scheduleStep(env)
    env.step()
    accumulated_reward += algorithm_module.getRewardByTask(env)
    print(f"Simulation time: {env.simulation_time}, ACC_Reward: {accumulated_reward}", end='\r')
    env.render()
env.close()
```

4. If another version of SUMO is used, please generate SUMO scenarios via the `sumo_dir/tools/osmWebWizard.py` and change related paths in `config.yaml`. Tested graphic videos:

Use Berlin as the map:

https://github.com/user-attachments/assets/570a2980-77b8-42a1-818d-3a6bc2efa83a

Use 五角场 in Shanghai as the map:

https://github.com/user-attachments/assets/f6d1b1ce-dfa7-4bef-b2ad-a0e1ccce91b1

5. For more personalized developing guidance, we attempt to build [AirFogSim Assistant-GPTs](https://chatgpt.com/g/g-uTOZnSsOr-airfogsim-assistant). However, GPT may make mistakes, remember to check the code.


<!-- use ./baselines/ -->
## Baselines:
### DQN for Task Offloading
This is a baseline implementation of the DQN algorithm for task offloading in a multi-agent system. The algorithm is implemented in the `dqn_offloading` directory. Please refer to the [README](./baselines/dqn_offloading/README.md) for more details. 
