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

# 让端口 8080 可用于容器外部
EXPOSE 8080

# 定义环境变量 (如果没有明确给定 PORT，默认绑定到 8080)
ENV PORT 8080
ENV PYTHONUNBUFFERED 1

# 使用 sh -c 启动 gunicorn，并确保 PORT 变量被正确解析
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:$PORT app:app --timeout 600"]
