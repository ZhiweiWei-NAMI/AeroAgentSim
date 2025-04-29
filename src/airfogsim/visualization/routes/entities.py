from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from airfogsim.utils.logging_config import get_logger
# 导入 data_service 和 sim_integration 实例
from ..app import data_service, sim_integration

# 创建路由器
router = APIRouter()

# 设置日志记录
logger = get_logger(__name__)

# 无人机数据API
@router.get("/drones")
async def get_drones():
    try:
        drones = data_service.get_all_drones()
        return drones
    except Exception as e:
        logger.error(f"获取无人机列表失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/drones/{drone_id}")
async def get_drone(drone_id: str):
    try:
        drone = data_service.get_drone(drone_id)
        if not drone:
            raise HTTPException(status_code=404, detail="未找到指定的无人机")
        return drone
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取无人机数据失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/drones/{drone_id}/history")
async def get_drone_history(
    drone_id: str, 
    start_time: Optional[float] = Query(None, description="开始时间(仿真时间)"),
    end_time: Optional[float] = Query(None, description="结束时间(仿真时间)"),
    limit: int = Query(100, description="最大记录数")
):
    try:
        history = data_service.get_drone_history(drone_id, start_time, end_time, limit)
        return history
    except Exception as e:
        logger.error(f"获取无人机历史数据失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/drones/{drone_id}/trajectory")
async def get_drone_trajectory(
    drone_id: str,
    start_time: Optional[float] = Query(None, description="开始时间(仿真时间)"),
    end_time: Optional[float] = Query(None, description="结束时间(仿真时间)"),
    interval: float = Query(1.0, description="采样间隔")
):
    try:
        trajectory = data_service.get_drone_trajectory(drone_id, start_time, end_time, interval)
        return trajectory
    except Exception as e:
        logger.error(f"获取无人机轨迹数据失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# 车辆数据API
@router.get("/vehicles")
async def get_vehicles():
    """获取当前仿真中所有车辆的最新状态"""
    try:
        # 从 TrafficDataProvider 获取车辆完整状态（位置和动态信息）
        if sim_integration.simulation_manager and sim_integration.simulation_manager.traffic_provider:
            # 使用新的接口获取所有车辆状态
            vehicles_list = sim_integration.simulation_manager.traffic_provider.get_all_vehicle_states()
            return vehicles_list
        else:
            # 如果 TrafficProvider 不可用，可以考虑回退到数据库（如果需要）
            # 或者直接返回空列表，表示当前没有实时车辆数据
            logger.info("TrafficProvider 不可用，无法获取实时车辆数据。")
            return []
            # 如果需要回退到数据库：
            # vehicles = data_service.get_all_vehicles()
            # return vehicles
    except Exception as e:
        logger.error(f"获取车辆列表失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/vehicles/{vehicle_id}")
async def get_vehicle(vehicle_id: str):
    """获取指定车辆的最新状态"""
    try:
        # 从 TrafficDataProvider 获取车辆完整状态（位置和动态信息）
        if sim_integration.simulation_manager and sim_integration.simulation_manager.traffic_provider:
            # 确保车辆ID格式正确
            if not vehicle_id.startswith('vehicle_'):
                vehicle_id = f'vehicle_{vehicle_id}'
                
            # 使用新的接口获取单个车辆状态
            vehicle = sim_integration.simulation_manager.traffic_provider.get_vehicle_state(vehicle_id)
            if vehicle:
                return vehicle
            else:
                # 在实时数据中未找到，可以认为车辆不存在或已离开
                raise HTTPException(status_code=404, detail=f"在实时仿真数据中未找到车辆 {vehicle_id}")
        else:
            # 如果 TrafficProvider 不可用
            logger.info(f"TrafficProvider 不可用，无法获取车辆 {vehicle_id} 的实时数据。")
            raise HTTPException(status_code=404, detail=f"TrafficProvider 不可用，无法获取车辆 {vehicle_id} 的实时数据")
            # 如果需要回退到数据库：
            # vehicle = data_service.get_vehicle(vehicle_id)
            # if not vehicle:
            #     raise HTTPException(status_code=404, detail="未找到指定的车辆")
            # return vehicle
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取车辆数据失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))