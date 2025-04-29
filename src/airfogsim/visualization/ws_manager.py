from fastapi import WebSocket
from typing import List
from airfogsim.utils.logging_config import get_logger

# 设置日志记录器
logger = get_logger(__name__)
logger.info("WebSocket连接管理器已初始化。")

# WebSocket连接管理
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket客户端连接，当前连接数: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        logger.info(f"WebSocket客户端断开，当前连接数: {len(self.active_connections)}")

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)