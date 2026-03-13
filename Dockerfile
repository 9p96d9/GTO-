FROM python:3.11-slim

# Node.js インストール
RUN apt-get update && apt-get install -y curl && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python依存インストール
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Node依存インストール
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

# プロジェクトファイルをコピー
COPY scripts/ ./scripts/
COPY static/ ./static/
COPY server.py ./

# 各種ディレクトリ作成
RUN mkdir -p input/done output data

ENV PYTHONIOENCODING=utf-8

# RailwayはPORTを自動設定する
CMD ["python", "server.py"]
