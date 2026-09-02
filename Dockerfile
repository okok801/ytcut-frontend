FROM python:3.12-slim

# 安裝 ffmpeg 與憑證、curl、unzip
RUN apt-get update && apt-get install -y ffmpeg ca-certificates curl unzip && rm -rf /var/lib/apt/lists/*

# 下載並安裝 Deno (yt-dlp 官方推薦且相容性 100% 的預設 JS 引擎)
RUN curl -fsSL https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip -o deno.zip \
    && unzip deno.zip -d /usr/local/bin \
    && rm deno.zip \
    && chmod +x /usr/local/bin/deno

WORKDIR /app

# 複製並安裝 Python 依賴
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 繞過群暉 Docker 快取 Bug，確保每次建置都複製最新程式碼
ENV CACHE_BYPASS_VER=7

# 複製整個專案檔案
COPY . .

WORKDIR /app/backend

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
