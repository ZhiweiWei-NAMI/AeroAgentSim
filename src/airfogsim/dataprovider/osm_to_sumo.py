#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
从OpenStreetMap数据生成SUMO路网文件的工具。

此模块提供了从OSM数据生成SUMO路网文件的功能，
可以根据指定的地理坐标和半径下载OSM数据，
然后转换为SUMO可用的路网文件。

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import os
import subprocess
from airfogsim.utils.logging_config import get_logger
import tempfile
import urllib.request
import shutil
import math
from pathlib import Path
import urllib.parse
import urllib.request

# 配置日志
logger = get_logger(__name__)

def download_osm_data(center_lat, center_lng, radius_km, output_file):
    """
    下载指定区域的OSM数据。
    
    Args:
        center_lat: 中心点纬度
        center_lng: 中心点经度
        radius_km: 半径（公里）
        output_file: 输出文件路径
        
    Returns:
        bool: 下载是否成功
    """
    # load_dotenv() 已移到全局作用域

    # 计算半径（米）
    radius_meters = radius_km * 1000.0
        
    # 构建 Overpass QL 查询字符串，使用 around 过滤器
    # 查询指定中心点和半径范围内的所有 highway 类型的 way 及其节点
    # [out:xml] 指定输出格式
    # [timeout:60] 设置超时时间
    # way(around:...) 查找半径内的 way
    # [highway] 过滤只保留 highway 类型的 way (道路)
    # (._;>;) 获取这些 way 的所有节点以及相关的 relation (递归向上查找)
    # out meta; 输出结果及元数据
    query = f"""
    [out:xml][timeout:60];
    (
      way(around:{radius_meters},{center_lat},{center_lng})[highway];
    );
    (._;>;);
    out meta;
    """
    
    logger.info(f"正在使用 Overpass API 查询中心点 ({center_lat}, {center_lng}) 半径 {radius_km}km 内的道路数据...")
    
    try:
        
        overpass_interpreter_url = "https://overpass-api.de/api/interpreter"
        # 对查询字符串进行 URL 编码
        encoded_query = urllib.parse.quote(query)
        full_query_url = f"{overpass_interpreter_url}?data={encoded_query}"

        logger.info(f"正在从 {overpass_interpreter_url} (使用QL查询) 下载OSM数据...")
        
        request = urllib.request.Request(
            full_query_url,
            headers={'Content-Type': 'application/x-www-form-urlencoded',
                'User-Agent': 'AirFogSim/1.0 (Python script)'} # 添加 User-Agent 可能有助于避免某些服务器限制
        )
        
        # 检查环境变量中是否有代理设置
        http_proxy = os.getenv('HTTP_PROXY')
        https_proxy = os.getenv('HTTPS_PROXY')

        opener = None
        if http_proxy or https_proxy:
            proxies = {}
            if http_proxy:
                proxies['http'] = http_proxy
            if https_proxy:
                proxies['https'] = https_proxy

            logger.info(f"检测到代理设置，将使用代理: {proxies}")
            proxy_handler = urllib.request.ProxyHandler(proxies)
            opener = urllib.request.build_opener(proxy_handler)
        else:
            logger.info("未检测到代理设置，将直接连接。")
            # 使用默认 opener
            opener = urllib.request.build_opener()

        # 使用 opener 发起请求，添加 timeout=60 参数
        try:
            with opener.open(request, timeout=60) as response, open(output_file, 'wb') as out_file:
                if response.getcode() == 200: # 注意：使用 opener 后获取状态码的方法是 getcode()
                    shutil.copyfileobj(response, out_file)
                    logger.info(f"OSM数据已成功下载到 {output_file}")
                    return True
                else:
                    logger.error(f"下载OSM数据失败: HTTP Status {response.getcode()}")
                    # 尝试读取错误信息
                    try:
                        error_content = response.read().decode('utf-8', errors='ignore')
                        logger.error(f"服务器响应: {error_content[:500]}...")
                    except Exception as read_err:
                        logger.error(f"无法读取错误响应内容: {read_err}")
                    return False
        except urllib.error.URLError as e:
             # 捕获由 opener.open 引发的更通用的 URLError，它可能是因为代理问题等
            logger.error(f"下载OSM数据时发生 URL 错误: {e.reason}")
            # 检查是否是 HTTPError 的实例以获取更具体的代码
            if isinstance(e, urllib.error.HTTPError):
                logger.error(f"HTTP Error Code: {e.code}")
                try:
                    error_body = e.read().decode('utf-8', errors='ignore')
                    logger.error(f"服务器错误详情: {error_body[:500]}...")
                except Exception as read_err:
                    logger.error(f"无法读取HTTP错误响应体: {read_err}")
            return False
        
    except urllib.error.HTTPError as e:
        # 捕获 urllib 的 HTTP 错误
        logger.error(f"下载OSM数据失败: HTTP Error {e.code} - {e.reason}")
        try:
            error_body = e.read().decode('utf-8', errors='ignore')
            logger.error(f"服务器错误详情: {error_body[:500]}...")
        except Exception as read_err:
            logger.error(f"无法读取HTTP错误响应体: {read_err}")
        return False
    except Exception as e:
        logger.error(f"下载OSM数据时发生未知错误: {e.__class__.__name__}: {e}")
        import traceback
        logger.error(traceback.format_exc()) # 记录详细的堆栈跟踪
        return False

def generate_sumo_network(osm_file, output_dir, prefix="osm_generated", vehicle_count=200):
    """
    从OSM数据生成SUMO路网文件。
    
    Args:
        osm_file: OSM数据文件路径
        output_dir: 输出目录
        prefix: 输出文件前缀
        vehicle_count: 生成的随机车辆数量上限，默认为200
        
    Returns:
        dict: 包含生成的文件路径
    """
    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)
    
    # 文件路径
    net_file = os.path.join(output_dir, f"{prefix}.net.xml")
    poly_file = os.path.join(output_dir, f"{prefix}.poly.xml")
    sumocfg_file = os.path.join(output_dir, f"{prefix}.sumocfg")
    
    # 检查SUMO工具是否可用
    try:
        subprocess.run(["netconvert", "--version"], stdout=subprocess.PIPE, check=True)
    except (subprocess.SubprocessError, FileNotFoundError):
        logger.error("未找到netconvert工具。请确保SUMO已正确安装。")
        return None
    
    # 使用netconvert从OSM生成网络文件
    logger.info("正在生成SUMO网络文件...")
    try:
        subprocess.run([
            "netconvert", 
            "--osm", osm_file,
            "--output", net_file,
            "--geometry.remove",
            "--roundabouts.guess",
            "--ramps.guess",
            "--junctions.join",
            "--tls.guess-signals",
            "--tls.discard-simple",
            "--tls.join",
            "--edges.join"
        ], check=True)
        
        logger.info(f"SUMO网络文件已生成: {net_file}")
    except subprocess.SubprocessError as e:
        logger.error(f"生成网络文件失败: {e}")
        return None
    
    # 使用polyconvert生成多边形文件（建筑物等）
    try:
        subprocess.run([
            "polyconvert",
            "--osm", osm_file,
            "--net", net_file,
            "--output", poly_file
        ], check=True)
        
        logger.info(f"SUMO多边形文件已生成: {poly_file}")
    except subprocess.SubprocessError as e:
        logger.error(f"生成多边形文件失败: {e}")
        # 继续执行，多边形文件不是必需的
    
    # 生成随机车辆路由
    routes_file = os.path.join(output_dir, f"{prefix}.rou.xml")
    # 读取 SUMO_HOME 环境变量
    sumo_home = os.getenv("SUMO_HOME")
    if not sumo_home:
        logger.error("未找到 SUMO_HOME 环境变量。无法定位 randomTrips.py。请在 .env 文件或系统环境中设置 SUMO_HOME。")
        return None
    
    random_trips_script = os.path.join(sumo_home, "tools", "randomTrips.py")
    if not os.path.exists(random_trips_script):
         logger.error(f"在指定的 SUMO_HOME ('{sumo_home}') 下未找到 randomTrips.py。预期路径: {random_trips_script}")
         return None
         
    logger.info(f"正在使用 {random_trips_script} 生成路由文件...")
    try:
        # 使用insertion-rate参数直接控制每小时进入仿真的车辆数量
        # 这比使用概率参数更直观
        
        # 如果指定了车辆数量，计算每小时的车辆生成率和仿真结束时间
        if vehicle_count is not None:
            # 计算每小时生成的车辆数量，假设在1小时内均匀分布
            hourly_rate = vehicle_count
            # 设置仿真结束时间为1小时，确保生成指定数量的车辆
            end_time = 3600  # 1小时 = 3600秒
            logger.info(f"设置车辆生成率为: {hourly_rate}辆/小时，车辆数量上限约为: {vehicle_count}，使用结束时间: {end_time}秒")
        else:
            # 默认值
            hourly_rate = 200  # 默认每小时生成200辆车
            end_time = 3600  # 默认1小时
            logger.info(f"使用默认车辆生成率: {hourly_rate}辆/小时，结束时间: {end_time}秒")
        
        # 构建命令参数列表
        cmd = [
            "python",  # 显式使用 python 来执行脚本可能更可靠
            random_trips_script,  # 使用完整路径
            "-n", net_file,
            "-o", routes_file,
            "--insertion-rate", str(hourly_rate),  # 每小时生成的车辆数量
            "--random",
            "-e", str(end_time),  # 仿真结束时间（秒）
            "--vehicle-class", "passenger",  # 默认生成小汽车
            "--prefix", "vehicle"  # 车辆ID前缀
        ]
        
        subprocess.run(cmd, check=True)
        
        logger.info(f"SUMO路由文件已生成: {routes_file}")
    except subprocess.SubprocessError as e:
        logger.error(f"生成路由文件失败: {e}")
        return None
    
    # 创建SUMO配置文件
    with open(sumocfg_file, 'w') as f:
        f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<configuration xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://sumo.dlr.de/xsd/sumoConfiguration.xsd">
    <input>
        <net-file value="{os.path.basename(net_file)}"/>
        <route-files value="{os.path.basename(routes_file)}"/>
        <additional-files value="{os.path.basename(poly_file)}"/>
    </input>
    <time>
        <begin value="0"/>
        <end value="{end_time}"/>
    </time>
    <processing>
        <ignore-route-errors value="true"/>
    </processing>
    <report>
        <verbose value="true"/>
        <duration-log.statistics value="true"/>
        <no-step-log value="true"/>
    </report>
    <gui_only>
        <gui-settings-file value=""/>
    </gui_only>
</configuration>
""")
    
    logger.info(f"SUMO配置文件已生成: {sumocfg_file}")
    
    return {
        "net_file": net_file,
        "poly_file": poly_file,
        "routes_file": routes_file,
        "sumocfg_file": sumocfg_file
    }

def osm_to_sumo(center_lat, center_lng, radius_km, output_dir, vehicle_count=100):
    """
    一键从OSM数据生成SUMO路网文件。
    
    Args:
        center_lat: 中心点纬度
        center_lng: 中心点经度
        radius_km: 半径（公里）
        output_dir: 输出目录
        vehicle_count: 生成的随机车辆数量上限，默认为200
        
    Returns:
        dict: 包含生成的文件路径，或None表示失败
    """
    # 创建临时目录
    with tempfile.TemporaryDirectory() as temp_dir:
        # 下载OSM数据
        osm_file = os.path.join(temp_dir, "osm_data.xml")
        if not download_osm_data(center_lat, center_lng, radius_km, osm_file):
            return None
        
        # 生成SUMO路网文件
        return generate_sumo_network(osm_file, output_dir, vehicle_count=vehicle_count)

# 命令行入口
if __name__ == "__main__":
    import sys
    import math
    # 39.9042 116.4074 1 /home/weizhiwei/data2/weizhiwei/airfogsim/airfogsim-project/frontend/public/data/traffic/sumocfg [可选:车辆数量]
    if len(sys.argv) < 5:
        logger.info("用法: python osm_to_sumo.py <中心点纬度> <中心点经度> <半径(公里)> <输出目录> [车辆数量上限]")
        sys.exit(1)
    
    center_lat = float(sys.argv[1])
    center_lng = float(sys.argv[2])
    radius_km = float(sys.argv[3])
    output_dir = sys.argv[4]
    # 解析可选的车辆数量参数
    vehicle_count = 500
    if len(sys.argv) >= 6:
        try:
            vehicle_count = int(sys.argv[5])
            logger.info(f"设置随机车辆数量上限为: {vehicle_count}")
        except ValueError:
            logger.error(f"警告: 车辆数量参数 '{sys.argv[5]}' 不是有效的整数，将使用默认值（200）")
    
    result = osm_to_sumo(center_lat, center_lng, radius_km, output_dir, vehicle_count=vehicle_count)
    if result:
        logger.info(f"SUMO路网文件生成成功，配置文件位于: {result['sumocfg_file']}")
    else:
        logger.error("SUMO路网文件生成失败")
        sys.exit(1)