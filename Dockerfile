FROM python:3.11-slim

# Node.js + Chromium依存ライブラリ インストール
RUN apt-get update && apt-get install -y curl \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libasound2 libpango-1.0-0 libcairo2 libx11-6 libxext6 \
    fonts-ipafont-gothic fonts-ipafont-mincho && \
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
COPY extension/ ./extension/
COPY templates/ ./templates/
COPY html/ ./html/
COPY routes/ ./routes/
COPY state.py pipelines.py server.py ./
COPY alembic/ ./alembic/
COPY alembic.ini ./

# 各種ディレクトリ作成
RUN mkdir -p input/done output data

ENV PYTHONIOENCODING=utf-8

# RailwayはPORTを自動設定する
CMD ["sh", "-c", "python -m alembic upgrade head && python server.py"]
