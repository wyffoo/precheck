# 使用官方 Python 运行时作为父镜像
FROM python:3.9-slim

# 设置工作目录
WORKDIR /app

# 复制当前目录的内容到容器中
COPY . /app

# 安装依赖项
RUN pip install --no-cache-dir -r requirements.txt

# 安装 gunicorn
RUN pip install gunicorn

# 让端口 8080 可用于容器外部（作为默认值）
EXPOSE 8080

# 定义环境变量
ENV PORT=8080
ENV PYTHONUNBUFFERED=1

# 复制 entrypoint 脚本并确保可执行
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 验证 entrypoint.sh 存在并可执行
RUN ls -l /entrypoint.sh

# 使用 entrypoint 脚本启动应用
ENTRYPOINT ["/entrypoint.sh"]
CMD []