#!/bin/bash
# 打印 PORT 变量以便调试
echo "PORT is set to: $PORT"

# 如果 PORT 未设置，默认为 8080
PORT=${PORT:-8080}

# 启动 Gunicorn
exec gunicorn --bind 0.0.0.0:$PORT app:app --timeout 600