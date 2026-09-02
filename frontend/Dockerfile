FROM python:3.12-slim

# 安裝 ffmpeg 與憑證、nodejs（yt-dlp 解析 YouTube 簽章必需）
RUN apt-get update && apt-get install -y ffmpeg ca-certificates nodejs && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 複製並安裝 Python 依賴
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 繞過群暉 Docker 快取 Bug，確保每次建置都複製最新程式碼
ENV CACHE_BYPASS_VER=3

# 複製整個專案檔案
COPY . .

WORKDIR /app/backend

# 建立下載資料夾
RUN mkdir -p downloads

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
