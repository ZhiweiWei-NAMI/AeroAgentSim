import sqlite3
from airfogsim.utils.logging_config import get_logger
from typing import Dict, List, Any, Optional, Tuple

# Import the store classes from the db subpackage
from .db import (
    AgentStore,
    DroneStore,
    EventStore,
    TaskStore,
    UserStore,
    VehicleStore,
    WorkflowStore
)

logger = get_logger(__name__)

class SimulationDataService:
    """
    数据服务层 Facade。
    管理数据库连接并提供对各个数据存储类的访问。
    """

    def __init__(self, db_path="lowspace_sim.db"):
        """
        初始化数据服务

        Args:
            db_path: 数据库路径。
        """
        self.db_path = db_path
        self.conn = None
        try:
            # 设置 check_same_thread=False 允许在不同线程访问同一个连接
            # 注意：SQLite 本身不是线程安全的，在多线程环境中需要确保适当的锁定机制
            self.conn = sqlite3.connect(db_path, check_same_thread=False)
            logger.info(f"Database connection established to {db_path}")
            self._initialize_stores()
            self._initialize_db_tables()
        except sqlite3.Error as e:
            logger.error(f"Error connecting to database {db_path}: {e}")
            # Propagate the error or handle it as needed
            raise

    def _initialize_stores(self):
        """初始化所有数据存储类实例"""
        if not self.conn:
            # This should ideally not happen if constructor succeeded, but check anyway
            logger.error("Attempted to initialize stores without a database connection.")
            raise ConnectionError("Database connection is not established.")
        self.agent_store = AgentStore(self.conn)
        self.drone_store = DroneStore(self.conn)
        self.event_store = EventStore(self.conn)
        self.task_store = TaskStore(self.conn)
        self.user_store = UserStore(self.conn)
        self.vehicle_store = VehicleStore(self.conn)
        self.workflow_store = WorkflowStore(self.conn)
        logger.info("Data stores initialized.")

    def _initialize_db_tables(self):
        """初始化所有数据库表"""
        try:
            # Initialization order might matter if there are foreign keys,
            # but in this schema, it seems okay. User table is initialized first
            # due to the default user creation logic within its init.
            self.user_store.initialize_table() # Includes default user creation
            self.agent_store.initialize_table()
            self.drone_store.initialize_table()
            self.event_store.initialize_table()
            self.task_store.initialize_table()
            self.vehicle_store.initialize_table()
            self.workflow_store.initialize_table()
            logger.info("All database tables initialized.")
        except sqlite3.Error as e:
            logger.error(f"Error initializing database tables: {e}")
            raise

    # --- Facade Methods ---
    # Delegate calls to the appropriate store instance

    # Drone Methods
    def update_drone_state(self, drone_id: str, position: Tuple[float, float, float],
                           battery_level: float, status: str, speed: float = 0.0,
                           sim_time: float = 0.0):
        return self.drone_store.update_drone_state(drone_id, position, battery_level, status, speed, sim_time)

    def get_all_drones(self) -> List[Dict[str, Any]]:
        return self.drone_store.get_all_drones()

    def get_drone(self, drone_id: str) -> Optional[Dict[str, Any]]:
        return self.drone_store.get_drone(drone_id)

    def get_drone_history(self, drone_id: str, start_time: Optional[float] = None,
                          end_time: Optional[float] = None, limit: int = 100) -> List[Dict[str, Any]]:
        return self.drone_store.get_drone_history(drone_id, start_time, end_time, limit)

    def get_drone_trajectory(self, drone_id: str, start_time: Optional[float] = None,
                             end_time: Optional[float] = None, interval: float = 1.0) -> List[Dict[str, Any]]:
        return self.drone_store.get_drone_trajectory(drone_id, start_time, end_time, interval)

    def get_all_vehicles(self) -> List[Dict[str, Any]]:
        return self.vehicle_store.get_all_vehicles()

    def get_vehicle(self, vehicle_id: str) -> Optional[Dict[str, Any]]:
        return self.vehicle_store.get_vehicle(vehicle_id)

    # Workflow Methods
    def update_workflow(self, workflow_id: str, name: str, type_: str, agent_id: Optional[str],
                       status: str, details: Optional[Dict[str, Any]] = None):
        return self.workflow_store.update_workflow(workflow_id, name, type_, agent_id, status, details)

    def add_workflow(self, workflow: Dict[str, Any]):
        return self.workflow_store.add_workflow(workflow)

    def get_all_workflows(self) -> List[Dict[str, Any]]:
        return self.workflow_store.get_all_workflows()

    def get_workflow(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        return self.workflow_store.get_workflow(workflow_id)

    def delete_workflow(self, workflow_id: str) -> bool:
        return self.workflow_store.delete_workflow(workflow_id)

    # Agent Methods
    def update_agent(self, agent_id: str, name: str, type_: str,
                     position: Optional[Tuple[float, float, float]] = None,
                     properties: Optional[Dict[str, Any]] = None):
        return self.agent_store.update_agent(agent_id, name, type_, position, properties)

    def add_agent(self, agent: Dict[str, Any]):
        return self.agent_store.add_agent(agent)

    def get_all_agents(self) -> List[Dict[str, Any]]:
        return self.agent_store.get_all_agents()

    def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        return self.agent_store.get_agent(agent_id)

    def delete_agent(self, agent_id: str) -> bool:
        return self.agent_store.delete_agent(agent_id)

    # Task Methods
    def update_task(self, task_id: str, agent_id: str, name: str, type_: str,
                    status: str, workflow_id: Optional[str] = None,
                    progress: Optional[float] = None,
                    details: Optional[Dict[str, Any]] = None,
                    start_time: Optional[float] = None,
                    end_time: Optional[float] = None):
        return self.task_store.update_task(task_id, agent_id, name, type_, status, workflow_id, progress, details, start_time, end_time)

    def get_agent_tasks(self, agent_id: str) -> List[Dict[str, Any]]:
        return self.task_store.get_agent_tasks(agent_id)

    # Event Methods
    def log_event(self, source_id: str, event_type: str,
                  event_data: Optional[Dict[str, Any]] = None,
                  sim_time: Optional[float] = None):
        return self.event_store.log_event(source_id, event_type, event_data, sim_time)

    def get_events(self, source_id: Optional[str] = None, event_type: Optional[str] = None,
                   start_time: Optional[float] = None, end_time: Optional[float] = None,
                   limit: int = 100) -> List[Dict[str, Any]]:
        return self.event_store.get_events(source_id, event_type, start_time, end_time, limit)

    # User Methods
    def verify_user(self, username: str, password: str) -> bool:
        # Note: Ensure UserStore uses proper password hashing in production
        return self.user_store.verify_user(username, password)

    # Data Clearing Method
    def clear_data(self):
        """
        清除所有仿真相关的数据（不包括用户数据）。
        调用各个 store 的 clear_data 方法。
        """
        logger.warning("Clearing simulation data from all stores (excluding users)...")
        try:
            # Clear data from each store, except potentially UserStore
            self.drone_store.clear_data()
            self.vehicle_store.clear_data()
            self.workflow_store.clear_data()
            self.agent_store.clear_data()
            self.event_store.clear_data()
            self.task_store.clear_data()
            # self.user_store.clear_data() # Typically do not clear user data
            logger.info("Simulation data cleared successfully.")
        except sqlite3.Error as e:
            logger.error(f"Error during data clearing: {e}")
            # Decide if partial clearing requires rollback or specific handling
            raise

    def close(self):
        """关闭数据库连接"""
        if self.conn:
            try:
                self.conn.close()
                logger.info("Database connection closed.")
                self.conn = None
            except sqlite3.Error as e:
                logger.error(f"Error closing database connection: {e}")

    def __del__(self):
        """确保在对象销毁时关闭连接"""
        self.close()
