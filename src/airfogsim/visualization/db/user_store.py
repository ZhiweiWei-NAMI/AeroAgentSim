import sqlite3
from datetime import datetime
from airfogsim.utils.logging_config import get_logger
# Consider using a proper password hashing library like passlib or werkzeug.security
# For simplicity, this example continues with plain text, which is NOT secure.
# from passlib.hash import pbkdf2_sha256 as hasher

logger = get_logger(__name__)

class UserStore:
    """处理用户数据的存储和验证"""

    def __init__(self, conn: sqlite3.Connection):
        """
        初始化 UserStore

        Args:
            conn: 数据库连接对象
        """
        self.conn = conn

    def initialize_table(self):
        """初始化 users 表"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL, -- Store hash, not plain text!
                is_admin BOOLEAN NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            ''')
            self.conn.commit()
            logger.info("users table initialized.")
            self._init_default_user() # Initialize default user after table creation
        except sqlite3.Error as e:
            logger.error(f"Error initializing users table: {e}")
            raise

    def _init_default_user(self):
        """初始化默认管理员用户（如果不存在）"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('SELECT id FROM users WHERE username = ?', ('admin',))
            if not cursor.fetchone():
                # WARNING: Storing plain text password 'admin123'. Replace with hashing.
                # hashed_password = hasher.hash('admin123') # Example with hashing
                hashed_password = 'admin123' # Placeholder for plain text
                cursor.execute('''
                INSERT INTO users (username, password_hash, is_admin, created_at)
                VALUES (?, ?, ?, ?)
                ''', ('admin', hashed_password, True, datetime.now().isoformat()))
                self.conn.commit()
                logger.info("Default admin user 'admin' created.")
        except sqlite3.Error as e:
            logger.error(f"Error initializing default user: {e}")
            # Don't raise here, as failure to create default user might not be critical

    def verify_user(self, username: str, password: str) -> bool:
        """验证用户凭据"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('SELECT password_hash FROM users WHERE username = ?', (username,))
            result = cursor.fetchone()
            if result:
                stored_hash = result[0]
                # WARNING: Comparing plain text password. Replace with hash verification.
                # return hasher.verify(password, stored_hash) # Example with hashing
                return stored_hash == password # Placeholder for plain text comparison
            return False
        except sqlite3.Error as e:
            logger.error(f"Error verifying user {username}: {e}")
            return False

    # Add methods for creating, updating, deleting users if needed
    # def create_user(...)
    # def update_user(...)
    # def delete_user(...)

    def clear_data(self):
        """清除此存储相关的所有数据 (Use with caution!)"""
        # Typically, you might not want to clear user data with simulation data.
        # Decide if this method is appropriate for UserStore.
        # If kept, ensure it's called intentionally.
        cursor = self.conn.cursor()
        try:
            # Be very careful with this operation!
            # cursor.execute('DELETE FROM users')
            # self.conn.commit()
            # logger.warning("Cleared all data from users table. This is usually not intended.")
            logger.info("UserStore clear_data called, but deletion is commented out for safety.")
            pass # By default, do not clear users table
        except sqlite3.Error as e:
            logger.error(f"Error clearing users data: {e}")
            raise