from fastapi import WebSocket, WebSocketDisconnect
import json
import asyncio
from aeroagentsim.utils.logging_config import get_logger
from datetime import datetime
from ..app import manager, sim_integration

# 设置日志记录
logger = get_logger(__name__)

# WebSocket接口
async def websocket_endpoint(websocket: WebSocket):
    logger.info("WebSocket连接请求")
    await manager.connect(websocket)
    logger.info("WebSocket连接已建立")
    
    # 创建一个任务来定期检查队列
    queue_check_task = None
    
    # 发送初始状态
    try:
        initial_status = {
            "type": "sim_status",
            "status": sim_integration.simulation_status,
            "time": sim_integration.simulation_time,
            "speed": sim_integration.simulation_speed,
            "run_id": sim_integration.active_run_id,
        }
        await websocket.send_text(json.dumps(initial_status))
        logger.info(f"已发送初始状态: {initial_status}")
    except Exception as e:
        logger.error(f"发送初始状态失败: {str(e)}")
    
    async def check_update_queue():
        """定期检查更新队列并发送给客户端"""
        update_count = 0
        last_log_time = datetime.now()
        
        try:
            logger.info("开始更新队列检查任务")
            while True:
                try:
                    # 获取队列中的更新
                    updates = sim_integration.get_updates(max_items=20)
                    
                    # 发送所有更新给客户端
                    for update in updates:
                        try:
                            await websocket.send_text(json.dumps(update))
                            update_count += 1
                            
                            # 每100条消息或每30秒记录一次日志
                            current_time = datetime.now()
                            time_diff = (current_time - last_log_time).total_seconds()
                            if update_count % 100 == 0 or time_diff > 30:
                                logger.info(f"WebSocket已发送 {update_count} 条更新")
                                last_log_time = current_time
                                
                        except Exception as e:
                            logger.error(f"发送更新消息失败: {str(e)}")
                            # 如果发送失败，可能是连接已断开
                            return
                    
                    # 如果没有更新，短暂休眠
                    if not updates:
                        await asyncio.sleep(0.1)
                    
                    # 这个短暂停顿确保了其他任务有机会执行
                    await asyncio.sleep(0.05)
                    
                except Exception as e:
                    logger.error(f"处理更新队列时出错: {str(e)}")
                    # 短暂暂停后继续尝试
                    await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            logger.info(f"更新队列检查任务被取消，共发送了 {update_count} 条更新")
            raise
        except Exception as e:
            logger.error(f"更新队列检查任务异常终止: {str(e)}")
    
    try:
        # 启动队列检查任务
        queue_check_task = asyncio.create_task(check_update_queue())
        logger.info("队列检查任务已启动")
        
        # WebSocket 仅用于状态推送；控制命令一律走 REST。
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
                message = json.loads(data)
                logger.info(f"收到客户端WebSocket消息: {message.get('type')}")
                await websocket.send_text(json.dumps({
                    "type": "ws_notice",
                    "status": "ignored",
                    "message": "WebSocket is output-only. Use REST endpoints for control commands."
                }))
            except asyncio.TimeoutError:
                continue
                
    except WebSocketDisconnect:
        logger.info("WebSocket连接断开")
        # 连接断开时取消队列检查任务并断开连接
        if queue_check_task:
            logger.info("正在取消队列检查任务")
            queue_check_task.cancel()
            try:
                await queue_check_task
            except asyncio.CancelledError:
                logger.info("队列检查任务已成功取消")
            except Exception as e:
                logger.error(f"取消队列检查任务时出错: {str(e)}")
        
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket处理中发生错误: {str(e)}")
        # 确保在发生错误时也能清理资源
        if queue_check_task:
            queue_check_task.cancel()
        manager.disconnect(websocket)
