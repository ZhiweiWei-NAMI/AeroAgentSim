from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel

# 数据模型
class DroneState(BaseModel):
    id: str
    position: List[float]
    battery_level: float
    status: str
    speed: Optional[float] = 0.0
    timestamp: Optional[str] = None

class VehicleState(BaseModel):
    id: str
    position: List[float]
    type: str
    speed: float = 0.0
    angle: float = 0.0
    timestamp: Optional[str] = None
class WorkflowConfig(BaseModel):
    name: str
    type: str
    parameters: Dict[str, Any]
    agent_id: Optional[str] = None

class AgentConfig(BaseModel):
    name: str
    type: str
    initial_position: List[float] = [0, 0, 0]
    initial_battery: float = 100.0
    components: List[str] = []
    properties: Dict[str, Any] = {}

class AirspaceConfig(BaseModel):
    x_range: Tuple[float, float]
    y_range: Tuple[float, float]
    altitude_range: Tuple[float, float]
    max_capacity: int
    attributes: Dict[str, Any] = {}

class FrequencyConfig(BaseModel):
    frequency_range: Tuple[float, float]
    bandwidth: float
    max_users: int
    power_limit: float
    attributes: Dict[str, Any] = {}

class LandingSpotConfig(BaseModel):
    location: Tuple[float, float, float]
    radius: float
    max_capacity: int
    has_charging: bool = False
    has_data_transfer: bool = False
    attributes: Dict[str, Any] = {}

class EnvironmentConfig(BaseModel):
    airspaces: List[AirspaceConfig] = []
    frequencies: List[FrequencyConfig] = []
    landing_spots: List[LandingSpotConfig] = []
    agents: List[Dict[str, Any]] = []
    workflows: List[Dict[str, Any]] = []
    simulation_time: Optional[float] = 0.0
    simulation_speed: Optional[float] = 1.0

# 用户验证模型
class UserCredentials(BaseModel):
    username: str
    password: str

# 验证响应
class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"