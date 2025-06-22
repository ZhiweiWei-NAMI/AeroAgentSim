import sqlite3
import json
from typing import Dict, List, Any, Optional
from datetime import datetime
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

class TaskStore:
    """处理任务数据的存储和检索"""

    def __init__(self, conn: sqlite3.Connection):
        """
        初始化 TaskStore

        Args:
            conn: 数据库连接对象
        """
        self.conn = conn

    def initialize_table(self):
        """初始化 tasks 表"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL UNIQUE,
                agent_id TEXT NOT NULL,
                workflow_id TEXT,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                status TEXT NOT NULL,
                progress REAL,
                details TEXT, -- Storing details as JSON string
                start_time REAL,
                end_time REAL,
                timestamp TEXT NOT NULL
            )
            ''')
            self.conn.commit()
            logger.info("tasks table initialized.")
        except sqlite3.Error as e:
            logger.error(f"Error initializing tasks table: {e}")
            raise

    def update_task(self, task_id: str, agent_id: str, name: str, type_: str,
                    status: str, workflow_id: Optional[str] = None,
                    progress: Optional[float] = None,
                    details: Optional[Dict[str, Any]] = None,
                    start_time: Optional[float] = None,
                    end_time: Optional[float] = None):
        """更新或插入任务状态"""
        cursor = self.conn.cursor()
        details_json = json.dumps(details) if details else None
        timestamp = datetime.now().isoformat()
        try:
            cursor.execute('''
            INSERT OR REPLACE INTO tasks
            (task_id, agent_id, workflow_id, name, type, status, progress, details, start_time, end_time, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                task_id,
                agent_id,
                workflow_id,
                name,
                type_,
                status,
                progress,
                details_json,
                start_time,
                end_time,
                timestamp
            ))
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error updating task {task_id} for agent {agent_id}: {e}")
            # Consider re-raising

    def get_agent_tasks(self, agent_id: str) -> List[Dict[str, Any]]:
        """获取智能体的所有任务"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('SELECT * FROM tasks WHERE agent_id = ?', (agent_id,))
            rows = cursor.fetchall()
            tasks = []
            for row in rows:
                details = None
                try:
                    if row[8]: # Check if details is not NULL
                        details = json.loads(row[8])
                except json.JSONDecodeError:
                    logger.warning(f"Could not decode details JSON for task {row[1]}: {row[8]}")
                    details = {"error": "Invalid JSON format"}

                tasks.append({
                    'id': row[1], # task_id
                    'agent_id': row[2],
                    'workflow_id': row[3],
                    'name': row[4],
                    'type': row[5],
                    'status': row[6],
                    'progress': row[7],
                    'details': details,
                    'start_time': row[9],
                    'end_time': row[10],
                    'timestamp': row[11]
                })
            return tasks
        except sqlite3.Error as e:
            logger.error(f"Error getting tasks for agent {agent_id}: {e}")
            return []

    def clear_data(self):
        """清除此存储相关的所有数据"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('DELETE FROM tasks')
            self.conn.commit()
            logger.info("Cleared all data from tasks table.")
        except sqlite3.Error as e:
            logger.error(f"Error clearing tasks data: {e}")
            raise