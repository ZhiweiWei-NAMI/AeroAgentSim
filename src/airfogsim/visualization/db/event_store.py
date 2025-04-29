import sqlite3
import json
from typing import Dict, List, Any, Optional
from datetime import datetime
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

class EventStore:
    """处理事件数据的存储和检索"""

    def __init__(self, conn: sqlite3.Connection):
        """
        初始化 EventStore

        Args:
            conn: 数据库连接对象
        """
        self.conn = conn

    def initialize_table(self):
        """初始化 events 表"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_data TEXT, -- Storing event_data as JSON string
                sim_time REAL,
                timestamp TEXT NOT NULL
            )
            ''')
            self.conn.commit()
            logger.info("events table initialized.")
        except sqlite3.Error as e:
            logger.error(f"Error initializing events table: {e}")
            raise

    def log_event(self, source_id: str, event_type: str,
                  event_data: Optional[Dict[str, Any]] = None,
                  sim_time: Optional[float] = None):
        """记录事件"""
        cursor = self.conn.cursor()
        event_data_json = json.dumps(event_data) if event_data else None
        timestamp = datetime.now().isoformat()
        try:
            cursor.execute('''
            INSERT INTO events
            (source_id, event_type, event_data, sim_time, timestamp)
            VALUES (?, ?, ?, ?, ?)
            ''', (
                source_id,
                event_type,
                event_data_json,
                sim_time,
                timestamp
            ))
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error logging event from {source_id} (type: {event_type}): {e}")
            # Consider re-raising

    def get_events(self, source_id: Optional[str] = None, event_type: Optional[str] = None,
                   start_time: Optional[float] = None, end_time: Optional[float] = None,
                   limit: int = 100) -> List[Dict[str, Any]]:
        """获取事件记录，支持按来源、类型、时间范围过滤"""
        cursor = self.conn.cursor()
        query = "SELECT * FROM events WHERE 1=1" # Start with a true condition
        params = []

        if source_id:
            query += " AND source_id = ?"
            params.append(source_id)
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        if start_time is not None:
            # Assuming start_time refers to sim_time
            query += " AND sim_time >= ?"
            params.append(start_time)
        if end_time is not None:
            # Assuming end_time refers to sim_time
            query += " AND sim_time <= ?"
            params.append(end_time)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        try:
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            events = []
            for row in rows:
                event_data = None
                try:
                    if row[3]: # Check if event_data is not NULL
                        event_data = json.loads(row[3])
                except json.JSONDecodeError:
                    logger.warning(f"Could not decode event_data JSON for event {row[0]}: {row[3]}")
                    event_data = {"error": "Invalid JSON format"}

                events.append({
                    'id': row[0],
                    'source_id': row[1],
                    'event_type': row[2],
                    'event_data': event_data,
                    'sim_time': row[4],
                    'timestamp': row[5]
                })
            return events
        except sqlite3.Error as e:
            logger.error(f"Error getting events: {e}")
            return []


    def clear_data(self):
        """清除此存储相关的所有数据"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('DELETE FROM events')
            self.conn.commit()
            logger.info("Cleared all data from events table.")
        except sqlite3.Error as e:
            logger.error(f"Error clearing events data: {e}")
            raise