#!/bin/bash
# 打印环境变量以便调试
echo "DEBUG: All environment variables:"
env
echo "DEBUG: PORT is set to: $PORT"

# 如果 PORT 未设置，默认为 8080
PORT=${PORT:-8080}
echo "DEBUG: Using PORT: $PORT"

# 启动 Gunicorn
exec gunicorn --bind 0.0.0.0:$PORT app:app --timeout 600