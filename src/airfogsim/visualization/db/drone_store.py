import sqlite3
import json
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

class DroneStore:
    """处理无人机状态数据的存储和检索"""

    def __init__(self, conn: sqlite3.Connection):
        """
        初始化 DroneStore

        Args:
            conn: 数据库连接对象
        """
        self.conn = conn

    def initialize_table(self):
        """初始化 drone_states 表"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS drone_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                drone_id TEXT NOT NULL,
                position TEXT NOT NULL,
                battery_level REAL NOT NULL,
                status TEXT NOT NULL,
                speed REAL,
                sim_time REAL NOT NULL,
                timestamp TEXT NOT NULL,
                UNIQUE(drone_id, sim_time)
            )
            ''')
            self.conn.commit()
            logger.info("drone_states table initialized.")
        except sqlite3.Error as e:
            logger.error(f"Error initializing drone_states table: {e}")
            raise

    def update_drone_state(self, drone_id: str, position: Tuple[float, float, float],
                           battery_level: float, status: str, speed: float = 0.0,
                           sim_time: float = 0.0):
        """更新无人机状态"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('''
            INSERT OR REPLACE INTO drone_states
            (drone_id, position, battery_level, status, speed, sim_time, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                drone_id,
                json.dumps(position),
                battery_level,
                status,
                speed,
                sim_time,
                datetime.now().isoformat()
            ))
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error updating drone state for {drone_id}: {e}")
            # 不重新抛出异常，允许服务继续运行，但记录错误
            # raise

    def get_all_drones(self) -> List[Dict[str, Any]]:
        """获取所有无人机的最新状态"""
        cursor = self.conn.cursor()
        try:
            # 查询每个无人机的最新状态记录
            cursor.execute('''
            SELECT ds1.* FROM drone_states ds1
            INNER JOIN (
                SELECT drone_id, MAX(sim_time) as max_time
                FROM drone_states
                GROUP BY drone_id
            ) ds2 ON ds1.drone_id = ds2.drone_id AND ds1.sim_time = ds2.max_time
            ''')
            rows = cursor.fetchall()
            drones = []
            for row in rows:
                drones.append({
                    'id': row[1],
                    'position': json.loads(row[2]),
                    'battery_level': row[3],
                    'status': row[4],
                    'speed': row[5],
                    'sim_time': row[6],
                    'timestamp': row[7]
                })
            return drones
        except sqlite3.Error as e:
            logger.error(f"Error getting all drones: {e}")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON for drone data: {e}")
            return []


    def get_drone(self, drone_id: str) -> Optional[Dict[str, Any]]:
        """获取指定无人机的最新状态"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('''
            SELECT * FROM drone_states
            WHERE drone_id = ?
            ORDER BY sim_time DESC LIMIT 1
            ''', (drone_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return {
                'id': row[1],
                'position': json.loads(row[2]),
                'battery_level': row[3],
                'status': row[4],
                'speed': row[5],
                'sim_time': row[6],
                'timestamp': row[7]
            }
        except sqlite3.Error as e:
            logger.error(f"Error getting drone {drone_id}: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON for drone {drone_id}: {e}")
            return None

    def get_drone_history(self, drone_id: str, start_time: Optional[float] = None,
                          end_time: Optional[float] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """获取无人机的历史状态记录"""
        cursor = self.conn.cursor()
        try:
            query = "SELECT * FROM drone_states WHERE drone_id = ?"
            params = [drone_id]

            if start_time is not None:
                # Assuming start_time is sim_time for consistency
                query += " AND sim_time >= ?"
                params.append(start_time)

            if end_time is not None:
                # Assuming end_time is sim_time for consistency
                query += " AND sim_time <= ?"
                params.append(end_time)

            query += " ORDER BY sim_time DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            history = []
            for row in rows:
                history.append({
                    'id': row[1],
                    'position': json.loads(row[2]),
                    'battery_level': row[3],
                    'status': row[4],
                    'speed': row[5],
                    'sim_time': row[6],
                    'timestamp': row[7]
                })
            return history
        except sqlite3.Error as e:
            logger.error(f"Error getting drone history for {drone_id}: {e}")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON for drone history {drone_id}: {e}")
            return []

    def get_drone_trajectory(self, drone_id: str, start_time: Optional[float] = None,
                             end_time: Optional[float] = None, interval: float = 1.0) -> List[Dict[str, Any]]:
        """获取无人机的轨迹数据"""
        cursor = self.conn.cursor()
        try:
            query = "SELECT position, sim_time FROM drone_states WHERE drone_id = ?"
            params = [drone_id]

            if start_time is not None:
                query += " AND sim_time >= ?"
                params.append(start_time)

            if end_time is not None:
                query += " AND sim_time <= ?"
                params.append(end_time)

            query += " ORDER BY sim_time"

            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()

            # 按指定间隔采样轨迹点
            trajectory = []
            last_included_time = -float('inf') # Initialize to negative infinity

            for row in rows:
                position_str, sim_time = row
                # Ensure sim_time is treated as float
                sim_time = float(sim_time)

                # 如果是第一个点或者与上一个点的时间间隔大于等于指定间隔，则包含该点
                if (sim_time - last_included_time) >= interval:
                    try:
                        position = json.loads(position_str)
                        trajectory.append({
                            'position': position,
                            'sim_time': sim_time
                        })
                        last_included_time = sim_time
                    except json.JSONDecodeError as e:
                         logger.error(f"Error decoding position JSON for drone {drone_id} at sim_time {sim_time}: {e}")
                         continue # Skip this point if JSON is invalid

            return trajectory
        except sqlite3.Error as e:
            logger.error(f"Error getting drone trajectory for {drone_id}: {e}")
            return []
        except Exception as e: # Catch potential errors during processing
            logger.error(f"Unexpected error getting drone trajectory for {drone_id}: {e}")
            return []

    def clear_data(self):
        """清除此存储相关的所有数据"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('DELETE FROM drone_states')
            self.conn.commit()
            logger.info("Cleared all data from drone_states table.")
        except sqlite3.Error as e:
            logger.error(f"Error clearing drone_states data: {e}")
            raise