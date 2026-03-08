FROM python:3.11-slim

# Node.js インストール
RUN apt-get update && apt-get install -y curl && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python依存（標準ライブラリのみなので追加インストール不要）
# Node依存（generate.js はfs/pathのみ使用 = 追加パッケージ不要）

# プロジェクトファイルをコピー
COPY scripts/ ./scripts/
COPY server.py run.py ./

# 各種ディレクトリ作成
RUN mkdir -p input/done output data

EXPOSE 5000

ENV PYTHONIOENCODING=utf-8

CMD ["python", "server.py"]
