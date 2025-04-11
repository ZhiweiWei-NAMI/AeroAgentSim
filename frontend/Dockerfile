# --- Build Stage ---
# 使用官方 Node.js 镜像作为构建环境
FROM node:18 as build-stage

# 设置工作目录
WORKDIR /app

# 复制 package.json 和 package-lock.json (或 yarn.lock)
# 使用 package-lock.json 以确保依赖版本一致性
COPY package.json package-lock.json ./

# 安装依赖 (使用 npm ci 更快且更可靠地安装锁定版本的依赖)
RUN npm ci

# 复制前端源代码
# 将 .dockerignore 文件添加到 frontend 目录可以排除 node_modules 等不需要复制的文件
COPY . .

# 构建生产版本的应用
# 检查 package.json 中的 build 脚本是否正确
RUN npm run build

# --- Production Stage (Optional, for verification or separate serving) ---
# 这个阶段主要是为了方便查看构建产物，实际部署时 Nginx 会直接使用 build-stage 的内容
FROM nginx:stable-alpine as production-stage

# 从构建阶段复制构建好的静态文件到 Nginx 的默认目录
COPY --from=build-stage /app/build /usr/share/nginx/html

# (可选) 复制自定义的 Nginx 配置文件 (如果需要)
# COPY nginx.conf /etc/nginx/nginx.conf

# 暴露 Nginx 端口
EXPOSE 80

# 启动 Nginx
CMD ["nginx", "-g", "daemon off;"]