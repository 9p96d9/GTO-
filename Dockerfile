FROM python:3.11-slim

# WeasyPrint依存ライブラリ（Node.js/Chromium不要）
RUN apt-get update && apt-get install -y \
    libpango-1.0-0 libcairo2 \
    libgdk-pixbuf2.0-0 libffi-dev \
    fonts-ipafont-gothic fonts-ipafont-mincho \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python依存インストール
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# プロジェクトファイルをコピー
COPY scripts/ ./scripts/
COPY static/ ./static/
COPY extension/ ./extension/
COPY templates/ ./templates/
COPY html_pages/ ./html_pages/
COPY routes/ ./routes/
COPY state.py pipelines.py server.py ./
COPY alembic/ ./alembic/
COPY alembic.ini ./

# 各種ディレクトリ作成
RUN mkdir -p input/done output data

ENV PYTHONIOENCODING=utf-8

# RailwayはPORTを自動設定する
CMD ["sh", "-c", "if [ -n \"$DATABASE_URL\" ]; then python -m alembic upgrade head; fi && python server.py"]
