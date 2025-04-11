#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AirFogSim可视化系统启动脚本
启动FastAPI后端和React前端服务
"""

import os
import sys
import subprocess
import time
import webbrowser
import signal
import argparse
from pathlib import Path

from dotenv import load_dotenv # 新增导入

# 加载 .env 文件中的环境变量 (如果存在) - 移到全局作用域
load_dotenv()
# 获取项目根目录
ROOT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT_DIR / "frontend"
BACKEND_MODULE = "src.airfogsim.visualization:app"

# 全局进程变量
frontend_process = None
backend_process = None

def signal_handler(sig, frame):
    """处理Ctrl+C信号，优雅地关闭所有进程"""
    print("\n正在关闭服务...")
    if frontend_process:
        try:
            if os.name == 'nt':  # Windows
                subprocess.call(['taskkill', '/F', '/T', '/PID', str(frontend_process.pid)])
            else:  # Linux/Mac
                os.killpg(os.getpgid(frontend_process.pid), signal.SIGTERM)
        except Exception as e:
            print(f"关闭前端进程时出错: {e}")
    
    if backend_process:
        try:
            if os.name == 'nt':  # Windows
                subprocess.call(['taskkill', '/F', '/T', '/PID', str(backend_process.pid)])
            else:  # Linux/Mac
                os.killpg(os.getpgid(backend_process.pid), signal.SIGTERM)
        except Exception as e:
            print(f"关闭后端进程时出错: {e}")
    
    print("所有服务已关闭")
    sys.exit(0)

def check_dependencies():
    """检查必要的依赖是否已安装"""
    try:
        # 检查Python依赖
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], 
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # 检查Node.js和npm
        if not os.path.exists(FRONTEND_DIR / "node_modules"):
            print("正在安装前端依赖，这可能需要几分钟时间...")
            os.chdir(FRONTEND_DIR)
            subprocess.check_call(["npm", "install"], 
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            os.chdir(ROOT_DIR)
    except subprocess.CalledProcessError as e:
        print(f"安装依赖失败: {e}")
        return False
    except FileNotFoundError as e:
        print(f"缺少必要的工具: {e}")
        print("请确保已安装Node.js和npm")
        return False
    
    return True

def start_backend(port=8000, reload=True):
    """启动FastAPI后端服务"""
    global backend_process
    
    print(f"正在启动后端服务 (端口: {port})...")
    cmd = [
        sys.executable, "-m", "uvicorn", 
        BACKEND_MODULE, 
        "--host", "0.0.0.0", 
        "--port", str(port)
    ]
    
    if reload:
        cmd.append("--reload")
    
    try:
        # 使用新进程组启动，以便能够正确终止子进程
        if os.name == 'nt':  # Windows
            backend_process = subprocess.Popen(cmd, 
                                              creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        else:  # Linux/Mac
            backend_process = subprocess.Popen(cmd, 
                                              preexec_fn=os.setsid)
        
        # 等待服务启动
        time.sleep(2)
        print(f"后端服务已启动: http://localhost:{port}")
        return True
    except Exception as e:
        print(f"启动后端服务失败: {e}")
        return False

def start_frontend(port=3000):
    """启动React前端开发服务器"""
    global frontend_process
    
    if not os.path.exists(FRONTEND_DIR):
        print(f"前端目录不存在: {FRONTEND_DIR}")
        return False
    
    print(f"正在启动前端服务 (端口: {port})...")
    os.chdir(FRONTEND_DIR)
    
    env = os.environ.copy()
    env["PORT"] = str(port)
    
    try:
        # 使用新进程组启动，以便能够正确终止子进程
        if os.name == 'nt':  # Windows
            frontend_process = subprocess.Popen(
                ["npm", "start"], 
                env=env,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:  # Linux/Mac
            frontend_process = subprocess.Popen(
                ["npm", "start"], 
                env=env,
                preexec_fn=os.setsid
            )
        
        # 返回到项目根目录
        os.chdir(ROOT_DIR)
        
        # 等待服务启动
        time.sleep(5)
        print(f"前端服务已启动: http://localhost:{port}")
        return True
    except Exception as e:
        print(f"启动前端服务失败: {e}")
        os.chdir(ROOT_DIR)  # 确保返回到根目录
        return False

def open_browser(url):
    """在默认浏览器中打开URL"""
    try:
        webbrowser.open(url)
        print(f"已在浏览器中打开: {url}")
    except Exception as e:
        print(f"无法打开浏览器: {e}")

def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="启动AirFogSim可视化系统")
    parser.add_argument("--backend-port", type=int, default=8002, help="后端服务端口")
    parser.add_argument("--frontend-port", type=int, default=3001, help="前端服务端口")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--no-reload", action="store_true", help="禁用后端自动重载")
    
    args = parser.parse_args()
    
    # 注册信号处理器，以便Ctrl+C能够优雅地关闭所有进程
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print("=" * 60)
    print("AirFogSim 可视化系统启动工具")
    print("=" * 60)
    
    # 检查依赖
    if not check_dependencies():
        print("依赖检查失败，无法启动服务")
        return 1
    
    # 启动后端
    backend_success = start_backend(port=args.backend_port, reload=not args.no_reload)
    
    # 启动前端
    frontend_success = start_frontend(port=args.frontend_port)
    
    if backend_success and frontend_success:
        print("\n所有服务已成功启动!")
        print(f"API文档: http://localhost:{args.backend_port}/docs")
        print(f"前端界面: http://localhost:{args.frontend_port}")
        print("\n按Ctrl+C可以关闭所有服务")
        
        # 自动打开浏览器
        if not args.no_browser:
            time.sleep(2)  # 给服务一点时间完全启动
            open_browser(f"http://localhost:{args.frontend_port}")
        
        # 保持脚本运行，直到用户按Ctrl+C
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            signal_handler(signal.SIGINT, None)
    else:
        print("\n服务启动失败")
        signal_handler(signal.SIGINT, None)
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())