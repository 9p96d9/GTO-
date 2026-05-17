#!/bin/bash
# ECS タスク定義を登録するスクリプト
# 実行前に ACCOUNT_ID と SECRET_ARN_SUFFIX を確認して書き換えること

set -e

REGION="ap-northeast-1"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
SECRET_NAME="gto/production"

# Secrets Manager の実際の ARN サフィックス（6文字）を取得
SECRET_ARN=$(aws secretsmanager describe-secret \
  --secret-id "$SECRET_NAME" \
  --region "$REGION" \
  --query ARN \
  --output text)

echo "Account ID  : $ACCOUNT_ID"
echo "Secret ARN  : $SECRET_ARN"

# task-definition.json の ACCOUNT_ID を実際の値に置換してテンポラリファイルを作成
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TMP=$(mktemp)
sed "s|ACCOUNT_ID|$ACCOUNT_ID|g" "$SCRIPT_DIR/task-definition.json" > "$TMP"

# ARN サフィックス付き置換（SECRET_ARN の末尾の -xxxxxx を含む形に修正）
# secrets の valueFrom を正確な ARN に書き換える
python3 - <<PYEOF
import json, re, sys

with open("$TMP") as f:
    td = json.load(f)

secret_arn = "$SECRET_ARN"
for s in td["containerDefinitions"][0]["secrets"]:
    key = s["name"]
    # arn:aws:...:gto/production:KEY:: → 実際の ARN に置換
    s["valueFrom"] = re.sub(
        r"arn:aws:secretsmanager:[^:]+:[^:]+:secret:gto/production",
        secret_arn,
        s["valueFrom"]
    )

with open("$TMP", "w") as f:
    json.dump(td, f, indent=2)
PYEOF

# タスク定義登録
echo "タスク定義を登録中..."
aws ecs register-task-definition \
  --region "$REGION" \
  --cli-input-json "file://$TMP"

rm "$TMP"
echo "完了: gto-task が登録されました"
