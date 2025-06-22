import sqlite3
import json
from typing import Dict, List, Any, Optional
from datetime import datetime
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

class WorkflowStore:
    """处理工作流数据的存储和检索"""

    def __init__(self, conn: sqlite3.Connection):
        """
        初始化 WorkflowStore

        Args:
            conn: 数据库连接对象
        """
        self.conn = conn

    def initialize_table(self):
        """初始化 workflows 表"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS workflows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                agent_id TEXT,
                status TEXT NOT NULL,
                details TEXT, -- Storing details as JSON string
                timestamp TEXT NOT NULL
            )
            ''')
            self.conn.commit()
            logger.info("workflows table initialized.")
        except sqlite3.Error as e:
            logger.error(f"Error initializing workflows table: {e}")
            raise

    def update_workflow(self, workflow_id: str, name: str, type_: str, agent_id: Optional[str],
                       status: str, details: Optional[Dict[str, Any]] = None):
        """更新或插入工作流状态"""
        cursor = self.conn.cursor()
        details_json = json.dumps(details) if details else None
        timestamp = datetime.now().isoformat()
        try:
            cursor.execute('''
            INSERT OR REPLACE INTO workflows
            (workflow_id, name, type, agent_id, status, details, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                workflow_id,
                name,
                type_,
                agent_id,
                status,
                details_json,
                timestamp
            ))
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error updating workflow {workflow_id}: {e}")
            # Consider if re-raising is appropriate

    def add_workflow(self, workflow: Dict[str, Any]):
        """添加工作流数据 (convenience method)"""
        # Ensure 'parameters' is used for details if present, otherwise empty dict
        details = workflow.get('parameters', workflow.get('details', {}))
        self.update_workflow(
            workflow['id'],
            workflow['name'],
            workflow['type'],
            workflow.get('agent_id'),
            workflow['status'],
            details
        )


    def get_all_workflows(self) -> List[Dict[str, Any]]:
        """获取所有工作流"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('SELECT * FROM workflows')
            rows = cursor.fetchall()
            workflows = []
            for row in rows:
                details = None
                try:
                    if row[6]: # Check if details column is not NULL
                        details = json.loads(row[6])
                except json.JSONDecodeError:
                    logger.warning(f"Could not decode details JSON for workflow {row[1]}: {row[6]}")
                    details = {"error": "Invalid JSON format"} # Or handle as needed

                workflows.append({
                    'id': row[1],
                    'name': row[2],
                    'type': row[3],
                    'agent_id': row[4],
                    'status': row[5],
                    'details': details,
                    'timestamp': row[7]
                })
            return workflows
        except sqlite3.Error as e:
            logger.error(f"Error getting all workflows: {e}")
            return []

    def get_workflow(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        """获取指定工作流"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('SELECT * FROM workflows WHERE workflow_id = ?', (workflow_id,))
            row = cursor.fetchone()
            if not row:
                return None

            details = None
            try:
                if row[6]:
                    details = json.loads(row[6])
            except json.JSONDecodeError:
                logger.warning(f"Could not decode details JSON for workflow {row[1]}: {row[6]}")
                details = {"error": "Invalid JSON format"}

            return {
                'id': row[1],
                'name': row[2],
                'type': row[3],
                'agent_id': row[4],
                'status': row[5],
                'details': details,
                'timestamp': row[7]
            }
        except sqlite3.Error as e:
            logger.error(f"Error getting workflow {workflow_id}: {e}")
            return None

    def delete_workflow(self, workflow_id: str) -> bool:
        """删除工作流"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('DELETE FROM workflows WHERE workflow_id = ?', (workflow_id,))
            affected = cursor.rowcount > 0
            self.conn.commit()
            if affected:
                logger.info(f"Deleted workflow {workflow_id}")
            else:
                logger.warning(f"Attempted to delete non-existent workflow {workflow_id}")
            return affected
        except sqlite3.Error as e:
            logger.error(f"Error deleting workflow {workflow_id}: {e}")
            return False # Indicate failure

    def clear_data(self):
        """清除此存储相关的所有数据"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('DELETE FROM workflows')
            self.conn.commit()
            logger.info("Cleared all data from workflows table.")
        except sqlite3.Error as e:
            logger.error(f"Error clearing workflows data: {e}")
            raise