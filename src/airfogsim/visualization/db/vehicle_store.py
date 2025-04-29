import sqlite3
import json
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

class VehicleStore:
    """处理车辆状态数据的存储和检索"""

    def __init__(self, conn: sqlite3.Connection):
        """
        初始化 VehicleStore

        Args:
            conn: 数据库连接对象
        """
        self.conn = conn

    def initialize_table(self):
        """初始化 vehicle_states 表"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS vehicle_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vehicle_id TEXT NOT NULL,
                position TEXT NOT NULL,
                type TEXT NOT NULL,
                speed REAL,
                angle REAL,
                sim_time REAL NOT NULL,
                timestamp TEXT NOT NULL,
                UNIQUE(vehicle_id, sim_time)
            )
            ''')
            self.conn.commit()
            logger.info("vehicle_states table initialized.")
        except sqlite3.Error as e:
            logger.error(f"Error initializing vehicle_states table: {e}")
            raise

    def update_vehicle_state(self, vehicle_id: str, position: Tuple[float, float, float],
                           type_: str, speed: float = 0.0, angle: float = 0.0,
                           sim_time: float = 0.0):
        """更新车辆状态"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('''
            INSERT OR REPLACE INTO vehicle_states
            (vehicle_id, position, type, speed, angle, sim_time, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                vehicle_id,
                json.dumps(position),
                type_,
                speed,
                angle,
                sim_time,
                datetime.now().isoformat()
            ))
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error updating vehicle state for {vehicle_id}: {e}")
            # Consider if re-raising is appropriate based on application needs
            # raise

    def get_all_vehicles(self) -> List[Dict[str, Any]]:
        """获取所有车辆的最新状态"""
        cursor = self.conn.cursor()
        try:
            # 查询每个车辆的最新状态记录
            cursor.execute('''
            SELECT vs1.* FROM vehicle_states vs1
            INNER JOIN (
                SELECT vehicle_id, MAX(sim_time) as max_time
                FROM vehicle_states
                GROUP BY vehicle_id
            ) vs2 ON vs1.vehicle_id = vs2.vehicle_id AND vs1.sim_time = vs2.max_time
            ''')
            rows = cursor.fetchall()
            vehicles = []
            for row in rows:
                vehicles.append({
                    'id': row[1],
                    'position': json.loads(row[2]),
                    'type': row[3],
                    'speed': row[4],
                    'angle': row[5],
                    'sim_time': row[6],
                    'timestamp': row[7]
                })
            return vehicles
        except sqlite3.Error as e:
            logger.error(f"Error getting all vehicles: {e}")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON for vehicle data: {e}")
            return []

    def get_vehicle(self, vehicle_id: str) -> Optional[Dict[str, Any]]:
        """获取指定车辆的最新状态"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('''
            SELECT * FROM vehicle_states
            WHERE vehicle_id = ?
            ORDER BY sim_time DESC LIMIT 1
            ''', (vehicle_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return {
                'id': row[1],
                'position': json.loads(row[2]),
                'type': row[3],
                'speed': row[4],
                'angle': row[5],
                'sim_time': row[6],
                'timestamp': row[7]
            }
        except sqlite3.Error as e:
            logger.error(f"Error getting vehicle {vehicle_id}: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON for vehicle {vehicle_id}: {e}")
            return None

    def clear_data(self):
        """清除此存储相关的所有数据"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('DELETE FROM vehicle_states')
            self.conn.commit()
            logger.info("Cleared all data from vehicle_states table.")
        except sqlite3.Error as e:
            logger.error(f"Error clearing vehicle_states data: {e}")
            raise