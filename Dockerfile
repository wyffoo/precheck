# 使用官方 Python 运行时作为父镜像
FROM python:3.9-slim

# 设置工作目录
WORKDIR /app

# 复制当前目录的内容到容器中
COPY . /app

# 安装依赖项
RUN pip install --no-cache-dir -r requirements.txt

# 安装 gunicorn（可选，但不建议使用 gunicorn，如果用 flask 的内建服务器）
RUN pip install gunicorn

# 让端口 8080 可用于容器外部
EXPOSE 8080

# 定义环境变量 (如果没有明确给定 PORT，默认绑定到 8080)
ENV PYTHONUNBUFFERED 1

# 使用 flask 的内建服务器启动应用
CMD ["flask", "run", "--host=0.0.0.0", "--port=$PORT"]
