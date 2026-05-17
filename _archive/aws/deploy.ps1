# deploy.ps1 - ECR push + ECS タスク定義登録
# GTO- フォルダのルートで実行すること:
#   cd C:\Users\user\Desktop\GTO-
#   .\aws\deploy.ps1

Set-StrictMode -Off
$ErrorActionPreference = "Stop"

$REGION     = "ap-northeast-1"
$REPO_NAME  = "gto-app"
$SECRET_ID  = "gto/production"
$TASK_FAMILY = "gto-task"

# ── 1. Account ID 取得 ────────────────────────────────────────────────────────
Write-Host "`n[1/5] AWS アカウント ID を取得..." -ForegroundColor Cyan
$ACCOUNT_ID = aws sts get-caller-identity --query Account --output text
Write-Host "      Account ID: $ACCOUNT_ID"

$ECR_URI = "$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$REPO_NAME"

# ── 2. ECR ログイン ───────────────────────────────────────────────────────────
Write-Host "`n[2/5] ECR にログイン..." -ForegroundColor Cyan
aws ecr get-login-password --region $REGION |
    docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"

# ── 3. Docker ビルド & push ───────────────────────────────────────────────────
Write-Host "`n[3/5] Docker イメージをビルド中（数分かかります）..." -ForegroundColor Cyan
docker build -t "${REPO_NAME}:latest" .
docker tag "${REPO_NAME}:latest" "${ECR_URI}:latest"

Write-Host "      ECR へ push 中..." -ForegroundColor Cyan
docker push "${ECR_URI}:latest"

# ── 4. Secrets Manager ARN 取得 ───────────────────────────────────────────────
Write-Host "`n[4/5] Secrets Manager ARN を取得..." -ForegroundColor Cyan
$SECRET_ARN = aws secretsmanager describe-secret `
    --secret-id $SECRET_ID `
    --region $REGION `
    --query ARN `
    --output text
Write-Host "      Secret ARN: $SECRET_ARN"

# ── 5. タスク定義を登録 ────────────────────────────────────────────────────────
Write-Host "`n[5/5] ECS タスク定義を登録..." -ForegroundColor Cyan

$tdJson = Get-Content "aws\task-definition.json" -Raw

# ACCOUNT_ID を置換
$tdJson = $tdJson -replace "ACCOUNT_ID", $ACCOUNT_ID

# secrets の valueFrom を実際の Secret ARN に置換
# 例: arn:aws:secretsmanager:ap-northeast-1:273949555510:secret:gto/production:KEY::
#  → arn:aws:secretsmanager:ap-northeast-1:273949555510:secret:gto/production-xxxxxx:KEY::
$tdJson = $tdJson -replace `
    "arn:aws:secretsmanager:${REGION}:${ACCOUNT_ID}:secret:${SECRET_ID}(:)", `
    "${SECRET_ARN}`$1"

$tmpFile = [System.IO.Path]::GetTempFileName() + ".json"
$tdJson | Out-File -FilePath $tmpFile -Encoding utf8

aws ecs register-task-definition `
    --region $REGION `
    --cli-input-json "file://$tmpFile"

Remove-Item $tmpFile

Write-Host "`n完了!" -ForegroundColor Green
Write-Host "ECR URI    : $ECR_URI"
Write-Host "Secret ARN : $SECRET_ARN"
Write-Host ""
Write-Host "次のステップ: AWSコンソールで ECS サービスを作成してください（STEP 11）"
Write-Host "  クラスター : gto-cluster"
Write-Host "  タスク定義 : $TASK_FAMILY"
