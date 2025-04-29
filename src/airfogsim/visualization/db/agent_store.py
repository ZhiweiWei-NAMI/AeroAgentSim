import sqlite3
import json
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

class AgentStore:
    """处理智能体数据的存储和检索"""

    def __init__(self, conn: sqlite3.Connection):
        """
        初始化 AgentStore

        Args:
            conn: 数据库连接对象
        """
        self.conn = conn

    def initialize_table(self):
        """初始化 agents 表"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS agents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                position TEXT, -- Storing position as JSON string
                properties TEXT, -- Storing properties as JSON string
                timestamp TEXT NOT NULL
            )
            ''')
            self.conn.commit()
            logger.info("agents table initialized.")
        except sqlite3.Error as e:
            logger.error(f"Error initializing agents table: {e}")
            raise

    def update_agent(self, agent_id: str, name: str, type_: str,
                     position: Optional[Tuple[float, float, float]] = None,
                     properties: Optional[Dict[str, Any]] = None):
        """更新或插入智能体状态"""
        cursor = self.conn.cursor()
        position_json = json.dumps(position) if position else None
        properties_json = json.dumps(properties) if properties else None
        timestamp = datetime.now().isoformat()
        try:
            cursor.execute('''
            INSERT OR REPLACE INTO agents
            (agent_id, name, type, position, properties, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                agent_id,
                name,
                type_,
                position_json,
                properties_json,
                timestamp
            ))
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error updating agent {agent_id}: {e}")
            # Consider re-raising

    def add_agent(self, agent: Dict[str, Any]):
        """添加智能体数据 (convenience method)"""
        self.update_agent(
            agent['id'],
            agent['name'],
            agent['type'],
            agent.get('position'),
            agent.get('properties', {}) # Default to empty dict if properties missing
        )

    def get_all_agents(self) -> List[Dict[str, Any]]:
        """获取所有智能体"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('SELECT * FROM agents')
            rows = cursor.fetchall()
            agents = []
            for row in rows:
                position = None
                properties = None
                try:
                    if row[4]: # Check if position is not NULL
                        position = json.loads(row[4])
                except json.JSONDecodeError:
                    logger.warning(f"Could not decode position JSON for agent {row[1]}: {row[4]}")
                    position = {"error": "Invalid JSON format"}
                try:
                    if row[5]: # Check if properties is not NULL
                        properties = json.loads(row[5])
                except json.JSONDecodeError:
                    logger.warning(f"Could not decode properties JSON for agent {row[1]}: {row[5]}")
                    properties = {"error": "Invalid JSON format"}

                agents.append({
                    'id': row[1],
                    'name': row[2],
                    'type': row[3],
                    'position': position,
                    'properties': properties,
                    'timestamp': row[6]
                })
            return agents
        except sqlite3.Error as e:
            logger.error(f"Error getting all agents: {e}")
            return []

    def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """获取指定智能体"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('SELECT * FROM agents WHERE agent_id = ?', (agent_id,))
            row = cursor.fetchone()
            if not row:
                return None

            position = None
            properties = None
            try:
                if row[4]:
                    position = json.loads(row[4])
            except json.JSONDecodeError:
                logger.warning(f"Could not decode position JSON for agent {row[1]}: {row[4]}")
                position = {"error": "Invalid JSON format"}
            try:
                if row[5]:
                    properties = json.loads(row[5])
            except json.JSONDecodeError:
                logger.warning(f"Could not decode properties JSON for agent {row[1]}: {row[5]}")
                properties = {"error": "Invalid JSON format"}

            return {
                'id': row[1],
                'name': row[2],
                'type': row[3],
                'position': position,
                'properties': properties,
                'timestamp': row[6]
            }
        except sqlite3.Error as e:
            logger.error(f"Error getting agent {agent_id}: {e}")
            return None

    def delete_agent(self, agent_id: str) -> bool:
        """删除智能体"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('DELETE FROM agents WHERE agent_id = ?', (agent_id,))
            affected = cursor.rowcount > 0
            self.conn.commit()
            if affected:
                logger.info(f"Deleted agent {agent_id}")
            else:
                logger.warning(f"Attempted to delete non-existent agent {agent_id}")
            return affected
        except sqlite3.Error as e:
            logger.error(f"Error deleting agent {agent_id}: {e}")
            return False

    def clear_data(self):
        """清除此存储相关的所有数据"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('DELETE FROM agents')
            self.conn.commit()
            logger.info("Cleared all data from agents table.")
        except sqlite3.Error as e:
            logger.error(f"Error clearing agents data: {e}")
            raise