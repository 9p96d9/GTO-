FROM python:3.11-slim

# Node.js インストール
RUN apt-get update && apt-get install -y curl && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python依存
RUN pip install --no-cache-dir watchdog

# プロジェクトファイルをコピー
COPY scripts/ ./scripts/
COPY front/ ./front/
COPY server.py run.py ./

# 各種ディレクトリ作成
RUN mkdir -p input/done output data

EXPOSE 5000

ENV PYTHONIOENCODING=utf-8

CMD ["watchmedo", "auto-restart", "--patterns=*.py", "--recursive", "--", "python", "server.py"]
