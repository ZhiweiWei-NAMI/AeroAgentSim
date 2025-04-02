import sqlite3
import json
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class SimulationDataService:
    """数据服务层，处理数据存储和检索"""
    
    def __init__(self, db_path="lowspace_sim.db"):
        """
        初始化数据服务
        
        Args:
            db_path: 数据库路径，默认使用项目根目录下的lowspace_sim.db文件
        """
        self.db_path = db_path
        # 设置check_same_thread=False允许在不同线程访问同一个连接
        # 注意：SQLite本身不是线程安全的，在多线程环境中需要确保适当的锁定机制
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.initialize_db()
        self._init_default_user()
    
    def initialize_db(self):
        """初始化数据库表"""
        cursor = self.conn.cursor()
        
        # 无人机状态表
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
        
        # 工作流表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS workflows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            agent_id TEXT,
            status TEXT NOT NULL,
            details TEXT,
            timestamp TEXT NOT NULL
        )
        ''')
        
        # 智能体表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            position TEXT,
            properties TEXT,
            timestamp TEXT NOT NULL
        )
        ''')
        
        # 事件表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_data TEXT,
            sim_time REAL,
            timestamp TEXT NOT NULL
        )
        ''')
        
        # 任务表
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
            details TEXT,
            start_time REAL,
            end_time REAL,
            timestamp TEXT NOT NULL
        )
        ''')
        
        self.conn.commit()
    
    # 无人机数据方法
    def update_drone_state(self, drone_id: str, position: Tuple[float, float, float],
                           battery_level: float, status: str, speed: float = 0.0,
                           sim_time: float = 0.0):
        """更新无人机状态"""
        cursor = self.conn.cursor()
        
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
    
    def get_all_drones(self) -> List[Dict[str, Any]]:
        """获取所有无人机的最新状态"""
        cursor = self.conn.cursor()
        
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
    
    def get_drone(self, drone_id: str) -> Optional[Dict[str, Any]]:
        """获取指定无人机的最新状态"""
        cursor = self.conn.cursor()
        
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
    
    def get_drone_history(self, drone_id: str, start_time: Optional[float] = None,
                          end_time: Optional[float] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """获取无人机的历史状态记录"""
        cursor = self.conn.cursor()
        
        query = "SELECT * FROM drone_states WHERE drone_id = ?"
        params = [drone_id]
        
        if start_time is not None:
            query += " AND timestamp >= ?"
            params.append(start_time)
        
        if end_time is not None:
            query += " AND timestamp <= ?"
            params.append(end_time)
        
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        
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
    
    def get_drone_trajectory(self, drone_id: str, start_time: Optional[float] = None,
                             end_time: Optional[float] = None, interval: float = 1.0) -> List[Dict[str, Any]]:
        """获取无人机的轨迹数据"""
        cursor = self.conn.cursor()
        
        query = "SELECT * FROM drone_states WHERE drone_id = ?"
        params = [drone_id]
        
        if start_time is not None:
            query += " AND sim_time >= ?"
            params.append(start_time)
        
        if end_time is not None:
            query += " AND sim_time <= ?"
            params.append(end_time)
        
        query += " ORDER BY sim_time"
        
        cursor.execute(query, params)
        
        rows = cursor.fetchall()
        
        # 按指定间隔采样轨迹点
        trajectory = []
        last_included_time = None
        
        for row in rows:
            sim_time = row[6]
            
            # 如果是第一个点或者与上一个点的时间间隔大于等于指定间隔，则包含该点
            if last_included_time is None or (sim_time - last_included_time) >= interval:
                trajectory.append({
                    'position': json.loads(row[2]),
                    'sim_time': sim_time
                })
                last_included_time = sim_time
        
        return trajectory
    
    # 工作流数据方法
    def update_workflow(self, workflow_id: str, name: str, type_: str, agent_id: Optional[str],
                       status: str, details: Optional[Dict[str, Any]] = None):
        """更新工作流状态"""
        cursor = self.conn.cursor()
        
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
            json.dumps(details) if details else None,
            datetime.now().isoformat()
        ))
        
        self.conn.commit()
    
    def get_all_workflows(self) -> List[Dict[str, Any]]:
        """获取所有工作流"""
        cursor = self.conn.cursor()
        
        cursor.execute('SELECT * FROM workflows')
        
        rows = cursor.fetchall()
        
        workflows = []
        for row in rows:
            workflows.append({
                'id': row[1],
                'name': row[2],
                'type': row[3],
                'agent_id': row[4],
                'status': row[5],
                'details': json.loads(row[6]) if row[6] else None,
                'timestamp': row[7]
            })
        
        return workflows
    
    def get_workflow(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        """获取指定工作流"""
        cursor = self.conn.cursor()
        
        cursor.execute('SELECT * FROM workflows WHERE workflow_id = ?', (workflow_id,))
        
        row = cursor.fetchone()
        
        if not row:
            return None
        
        return {
            'id': row[1],
            'name': row[2],
            'type': row[3],
            'agent_id': row[4],
            'status': row[5],
            'details': json.loads(row[6]) if row[6] else None,
            'timestamp': row[7]
        }
    
    def delete_workflow(self, workflow_id: str) -> bool:
        """删除工作流"""
        cursor = self.conn.cursor()
        
        cursor.execute('DELETE FROM workflows WHERE workflow_id = ?', (workflow_id,))
        
        affected = cursor.rowcount > 0
        self.conn.commit()
        return affected
    
    # 智能体数据方法
    def update_agent(self, agent_id: str, name: str, type_: str, 
                     position: Optional[Tuple[float, float, float]] = None,
                     properties: Optional[Dict[str, Any]] = None):
        """更新智能体状态"""
        cursor = self.conn.cursor()
        
        cursor.execute('''
        INSERT OR REPLACE INTO agents 
        (agent_id, name, type, position, properties, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            agent_id,
            name,
            type_,
            json.dumps(position) if position else None,
            json.dumps(properties) if properties else None,
            datetime.now().isoformat()
        ))
        
        self.conn.commit()
    
    def get_all_agents(self) -> List[Dict[str, Any]]:
        """获取所有智能体"""
        cursor = self.conn.cursor()
        
        cursor.execute('SELECT * FROM agents')
        
        rows = cursor.fetchall()
        
        agents = []
        for row in rows:
            agents.append({
                'id': row[1],
                'name': row[2],
                'type': row[3],
                'position': json.loads(row[4]) if row[4] else None,
                'properties': json.loads(row[5]) if row[5] else None,
                'timestamp': row[6]
            })
        
        return agents
    
    def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """获取指定智能体"""
        cursor = self.conn.cursor()
        
        cursor.execute('SELECT * FROM agents WHERE agent_id = ?', (agent_id,))
        
        row = cursor.fetchone()
        
        if not row:
            return None
        
        return {
            'id': row[1],
            'name': row[2],
            'type': row[3],
            'position': json.loads(row[4]) if row[4] else None,
            'properties': json.loads(row[5]) if row[5] else None,
            'timestamp': row[6]
        }
    
    def delete_agent(self, agent_id: str) -> bool:
        """删除智能体"""
        cursor = self.conn.cursor()
        
        cursor.execute('DELETE FROM agents WHERE agent_id = ?', (agent_id,))
        
        affected = cursor.rowcount > 0
        self.conn.commit()
        return affected
    
    # 任务数据方法
    def update_task(self, task_id: str, agent_id: str, name: str, type_: str, 
                    status: str, workflow_id: Optional[str] = None,
                    progress: Optional[float] = None, 
                    details: Optional[Dict[str, Any]] = None,
                    start_time: Optional[float] = None,
                    end_time: Optional[float] = None):
        """更新任务状态"""
        cursor = self.conn.cursor()
        
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
            json.dumps(details) if details else None,
            start_time,
            end_time,
            datetime.now().isoformat()
        ))
        
        self.conn.commit()
    
    def get_agent_tasks(self, agent_id: str) -> List[Dict[str, Any]]:
        """获取智能体的所有任务"""
        cursor = self.conn.cursor()
        
        cursor.execute('SELECT * FROM tasks WHERE agent_id = ?', (agent_id,))
        
        rows = cursor.fetchall()
        
        tasks = []
        for row in rows:
            tasks.append({
                'id': row[1],
                'agent_id': row[2],
                'workflow_id': row[3],
                'name': row[4],
                'type': row[5],
                'status': row[6],
                'progress': row[7],
                'details': json.loads(row[8]) if row[8] else None,
                'start_time': row[9],
                'end_time': row[10],
                'timestamp': row[11]
            })
        
        return tasks
    
    # 事件数据方法
    def log_event(self, source_id, event_data, event_type,
                 sim_time: Optional[float] = None):
        """记录事件"""
        cursor = self.conn.cursor()
        
        cursor.execute('''
        INSERT INTO events 
        (source_id, event_type, event_data, sim_time, timestamp)
        VALUES (?, ?, ?, ?, ?)
        ''', (
            source_id,
            event_type,
            json.dumps(event_data) if event_data else None,
            sim_time,
            datetime.now().isoformat()
        ))
        
        self.conn.commit()
        
    def add_agent(self, agent: Dict[str, Any]):
        """添加智能体数据"""
        self.update_agent(
            agent['id'],
            agent['name'],
            agent['type'],
            agent.get('position'),
            agent.get('properties', {})
        )
    
    def add_workflow(self, workflow: Dict[str, Any]):
        """添加工作流数据"""
        self.update_workflow(
            workflow['id'],
            workflow['name'],
            workflow['type'],
            workflow.get('agent_id'),
            workflow['status'],
            workflow.get('parameters', {})
        )
    
    def clear_data(self):
        """清除所有仿真数据"""
        cursor = self.conn.cursor()
        
        # 清空所有相关表
        tables = ['drone_states', 'workflows', 'agents', 'events', 'tasks']
        for table in tables:
            cursor.execute(f'DELETE FROM {table}')
        
        self.conn.commit()
        logger.info("所有仿真数据已清除")
    
    def _init_default_user(self):
        """初始化默认用户，方便开发调试时使用"""
        cursor = self.conn.cursor()
        
        # 创建用户表（如果不存在）
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_admin BOOLEAN NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        ''')
        
        # 检查是否已存在默认用户
        cursor.execute('SELECT * FROM users WHERE username = ?', ('admin',))
        if not cursor.fetchone():
            # 简单起见，这里直接存储明文密码'admin123'，生产环境应使用密码哈希
            cursor.execute('''
            INSERT INTO users (username, password_hash, is_admin, created_at)
            VALUES (?, ?, ?, ?)
            ''', ('admin', 'admin123', True, datetime.now().isoformat()))
            
            self.conn.commit()
            logger.info("已创建默认管理员用户")
            
    def verify_user(self, username: str, password: str) -> bool:
        """验证用户凭据"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT password_hash FROM users WHERE username = ?', (username,))
        result = cursor.fetchone()
        
        if result and result[0] == password:
            return True
        return False
    
    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
