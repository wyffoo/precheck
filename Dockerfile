# 使用官方 Python 镜像
FROM python:3.9-slim

# 安装系统依赖，包括 tesseract 和其他必需的库
RUN apt-get update && apt-get install -y tesseract-ocr && apt-get clean

# 安装所需的 Python 库
RUN pip install --upgrade pip
COPY requirements.txt /app/requirements.txt
WORKDIR /app
RUN pip install -r requirements.txt

# 将应用代码复制到容器内
COPY . /app

# 下载 NLTK 数据
RUN python -c "import nltk; nltk.download('punkt')"

# 设置工作目录并启动 Flask 应用
CMD ["python", "app.py"]
