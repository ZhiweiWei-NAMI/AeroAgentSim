# --- Build Stage ---
# 使用官方 Python 镜像作为基础
FROM python:3.10-slim as builder

# 设置工作目录
WORKDIR /app

# 安装构建依赖 (如果需要编译 C 扩展)
# RUN apt-get update && apt-get install -y --no-install-recommends gcc build-essential && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY pyproject.toml requirements.txt ./

# 安装项目依赖 (使用 pyproject.toml 和 requirements.txt)
# 优先使用 pyproject.toml 定义的核心依赖
RUN pip install --no-cache-dir .
# 安装 requirements.txt 中的额外依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目源代码
COPY src/ /app/src

# (可选) 如果你的项目需要编译或生成其他文件，在此处执行

# --- Runtime Stage ---
# 使用更小的 Python 镜像作为最终运行环境
FROM python:3.10-slim

# 设置工作目录
WORKDIR /app

# 从 builder 阶段复制安装好的依赖
COPY --from=builder /usr/local/lib/python3.10/site-packages /usr/local/lib/python3.10/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 复制项目源代码 (只需要运行时需要的文件)
COPY src/ /app/src
COPY main_for_visualization.py /app/

# (可选) 复制其他运行时需要的文件，例如配置文件

# 安装 gunicorn (已在 pyproject.toml 中声明)
# RUN pip install --no-cache-dir gunicorn

# 暴露 Gunicorn 运行的端口 (例如 8000)
EXPOSE 8000

# 设置环境变量 (如果需要)
# ENV FLASK_APP=src.airfogsim.visualization.app:create_app()
# ENV FLASK_ENV=production

# 运行 Gunicorn (你需要根据你的 Flask app 入口调整)
# 假设你的 Flask app 实例在 src/airfogsim/visualization/app.py 的 create_app() 函数中创建
# 或者你的主运行文件是 main_for_visualization.py
# 使用 gunicorn 启动 FastAPI 应用，指定 uvicorn worker
# 'src.airfogsim.visualization.app:app' 假设 FastAPI app 实例名为 'app'
# 你可能需要根据实际的应用实例名称调整 'app' 部分
CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "src.airfogsim.visualization.app:app"]