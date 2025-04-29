import threading
import time
import logging
from simpy.core import StopSimulation
from simpy.events import Event, Timeout
from airfogsim.core.environment import Environment
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

class PausableEnvironment(Environment):
    """可暂停的仿真环境，扩展自airfogsim的Environment"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pause_at = float('inf')
        self._paused = False
        self._pause_event = threading.Event()
        self._resume_event = threading.Event()
        # 初始化为已恢复状态
        self._resume_event.set()
        # 添加速度控制
        self._speed = 1.0
        self._last_step_time = None
        # 默认步进间隔，单位秒。这个值会根据速度调整
        # 基础间隔，对应速度为1x
        self._base_step_interval = 0.01
        self._step_interval = self._base_step_interval / self._speed

    def pause_at(self, time):
        """设置在特定时间暂停仿真"""
        self._pause_at = time

    def pause_now(self):
        """立即暂停仿真"""
        if not self._paused:
            self._paused = True
            self._pause_event.set()
            self._resume_event.clear()
            logger.info(f"仿真暂停于时间: {self.now}")

    def resume(self):
        """恢复仿真"""
        if self._paused:
            self._paused = False
            self._pause_at = float('inf') # 清除暂停点
            self._pause_event.clear()
            self._resume_event.set()
            logger.info(f"仿真恢复于时间: {self.now}")

    def set_speed(self, speed):
        """设置仿真速度"""
        if speed <= 0:
            raise ValueError("仿真速度必须大于0")
        self._speed = speed
        # 根据速度调整步进间隔
        # 速度越快，实际等待时间越短
        self._step_interval = self._base_step_interval / speed
        logger.info(f"仿真速度设置为 {speed}x, 步进间隔调整为 {self._step_interval:.4f} 秒")

    def step(self):
        """
        执行一步仿真，添加速度控制和暂停逻辑。
        """
        # --- 速度控制 ---
        current_real_time = time.monotonic() # 使用 monotonic time 避免系统时间调整影响

        # 如果有上一步的时间记录，根据速度控制添加延时
        if self._last_step_time is not None:
            # 计算两步之间的实际时间差
            elapsed_real_time = current_real_time - self._last_step_time

            # 计算期望的步进间隔 (基于仿真速度)
            # 注意：self._step_interval 已经考虑了速度
            expected_interval = self._step_interval

            # 如果实际时间差小于期望的步进间隔，则等待
            wait_time = expected_interval - elapsed_real_time
            if wait_time > 0:
                time.sleep(wait_time)

        # 更新上一步的时间戳 *在* 可能的等待之后，*在* 执行 super().step() 之前
        # 这样可以更准确地测量 step() 本身的执行时间（虽然通常很短）
        # 或者放在最后，测量包括 step() 在内的总时间
        # self._last_step_time = time.monotonic() # 放在这里或最后都可以

        # --- 暂停逻辑 ---
        # 如果已暂停，等待恢复信号 (在 run() 中处理等待，step() 只执行)
        # if self._paused:
        #     logger.debug("Step called while paused, waiting for resume...")
        #     self._resume_event.wait() # 不应在 step() 中阻塞

        # --- 执行原始的step逻辑 ---
        try:
            # 检查是否有事件可执行
            if self.peek() == float('inf'):
                 logger.debug(f"Step called at time {self.now}, but no events scheduled.")
                 # 如果没有事件，不调用 super().step() 可能导致时间不推进
                 # SimPy 的 step() 会处理这种情况，推进时间到下一个事件或保持不变
                 pass # 让 super().step() 处理

            # 调用父类的 step 方法
            super().step()

        except StopSimulation as e:
            logger.info(f"Simulation stopped during step at time {self.now}: {e}")
            raise # Re-raise to allow run() to catch it
        except Exception as e:
            logger.error(f"Error during simulation step at time {self.now}: {e}", exc_info=True)
            raise # Re-raise the exception

        # 更新上一步的时间戳（放在最后，包含 step() 执行时间）
        self._last_step_time = time.monotonic()


    def run(self, until=None):
        """重写run方法，支持暂停和恢复，并集成速度控制"""
        if until is not None:
            # Ensure 'until' is treated as simulation time
            if isinstance(until, (Event, Timeout)):
                 # If 'until' is an event, get its scheduled time if possible
                 # This might be complex depending on the event type
                 # For simplicity, we might just use a large number if it's an event
                 # Or handle specific event types if needed
                 logger.warning("Running until an Event object is complex with pausing. Using target time if available.")
                 # Try to get target time if it's a Timeout event
                 target_time = getattr(until, '_delay', float('inf')) if isinstance(until, Timeout) else float('inf')
            else:
                 target_time = float(until) # Convert to float for comparison
        else:
            target_time = float('inf')

        self._target_time = target_time
        logger.debug(f"Starting run loop. Current time: {self.now}, Target time: {self._target_time}")

        try:
            while True:
                # 1. 检查是否需要暂停 (在执行下一步之前)
                if self.now >= self._pause_at and not self._paused:
                    self.pause_now()

                # 2. 如果已暂停，等待恢复信号
                if self._paused:
                    logger.info(f"Simulation paused at {self.now}. Waiting for resume...")
                    self._resume_event.wait() # Blocking wait for resume signal
                    logger.info(f"Simulation resumed at {self.now}.")
                    # 重置 last_step_time 避免恢复后立即大跳跃
                    self._last_step_time = time.monotonic()

                # 3. 检查是否达到目标时间
                next_event_time = self.peek()
                if next_event_time >= self._target_time:
                    logger.debug(f"Next event time {next_event_time} >= target time {self._target_time}. Exiting run loop.")
                    # Advance time to the target if needed, but not beyond
                    if self._target_time > self.now and self._target_time != float('inf'):
                         # 不能直接设置self.now，使用timeout来推进时间
                         delay = self._target_time - self.now
                         # 创建一个Timeout事件，但不等待它
                         self.timeout(delay)
                         # 执行一步来处理新创建的Timeout
                         super().step()
                    break # Exit loop

                # 4. 检查是否没有更多事件
                if next_event_time == float('inf'):
                    logger.debug(f"No more events scheduled at time {self.now}. Exiting run loop.")
                    break # Exit loop

                # 5. 执行下一个事件 (调用包含速度控制的 step)
                self.step()

        except StopSimulation as exc:
            logger.info(f"Simulation stopped by StopSimulation event: {exc.args}")
            # StopSimulation usually provides the time it occurred
            return exc.args[0] if exc.args else self.now
        except Exception as e:
            logger.error(f"Exception during simulation run at time {self.now}: {e}", exc_info=True)
            # Depending on desired behavior, you might want to stop or continue
            raise # Re-raise the exception

        logger.debug(f"Exiting run loop normally. Final time: {self.now}")
        return self.now # Return the final simulation time