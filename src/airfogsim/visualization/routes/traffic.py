from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel
from airfogsim.utils.logging_config import get_logger
import os
from pathlib import Path

# 创建路由器
router = APIRouter()
# 定义请求体模型
class SumoNetworkRequest(BaseModel):
    center_lat: float
    center_lng: float
    radius_km: float

logger = get_logger(__name__)

# 交通仿真相关API
@router.post("/generate_sumo_network")
async def generate_sumo_network(request_data: SumoNetworkRequest):
    """
    从OSM数据生成SUMO路网文件
    
    Args:
        center_lat: 中心点纬度
        center_lng: 中心点经度
        radius_km: 半径（公里）
        
    Returns:
        生成的SUMO文件路径信息
    """
    print(f"生成SUMO路网文件: 中心点({request_data.center_lat}, {request_data.center_lng}), 半径{request_data.radius_km}公里")
    try:
        import sys
        
        # 导入OSM到SUMO转换模块
        sys.path.append(str(Path(__file__).parent.parent.parent.parent))
        from airfogsim.dataprovider.osm_to_sumo import osm_to_sumo
        
        # 确保输出目录存在
        output_dir = Path("frontend/public/data/traffic/sumocfg")
        os.makedirs(output_dir, exist_ok=True)
        
        # 生成SUMO路网文件
        result = osm_to_sumo(request_data.center_lat, request_data.center_lng, request_data.radius_km, output_dir)
        
        if not result:
            raise HTTPException(status_code=500, detail="生成SUMO路网文件失败")
        
        # 返回相对路径
        relative_paths = {}
        for key, path in result.items():
            relative_path = os.path.relpath(path, "frontend/public")
            relative_paths[key] = f"static/{relative_path}"
        
        return {
            "status": "success",
            "message": "SUMO路网文件生成成功",
            "files": relative_paths
        }
    except Exception as e:
        logger.error(f"生成SUMO路网文件失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sumo_configs")
async def list_sumo_configs():
    """
    列出可用的SUMO配置文件
    
    Returns:
        可用的SUMO配置文件列表
    """
    try:
        # SUMO配置文件目录
        sumocfg_dir = Path("frontend/public/data/traffic/sumocfg")
        os.makedirs(sumocfg_dir, exist_ok=True)
        
        # 列出所有.sumocfg文件
        sumocfg_files = list(sumocfg_dir.glob("*.sumocfg"))
        
        # 返回相对路径
        result = []
        for file_path in sumocfg_files:
            relative_path = os.path.relpath(file_path, "frontend/public")
            result.append({
                "name": file_path.name,
                "path": f"static/{relative_path}"
            })
        
        return result
    except Exception as e:
        logger.error(f"列出SUMO配置文件失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/csv_files")
async def list_traffic_csv_files():
    """
    列出可用的交通流CSV文件
    
    Returns:
        可用的交通流CSV文件列表
    """
    try:
        # CSV文件目录
        csv_dir = Path("frontend/public/data/traffic/file")
        os.makedirs(csv_dir, exist_ok=True)
        
        # 列出所有.csv文件
        csv_files = list(csv_dir.glob("*.csv"))
        
        # 返回相对路径
        result = []
        for file_path in csv_files:
            relative_path = os.path.relpath(file_path, "frontend/public")
            result.append({
                "name": file_path.name,
                "path": f"static/{relative_path}"
            })
        
        return result
    except Exception as e:
        logger.error(f"列出交通流CSV文件失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))