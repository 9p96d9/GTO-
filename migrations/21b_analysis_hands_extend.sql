-- Phase 21-B: analysis_hands 拡張 + analyses に hand_ids 追加
-- EC2上で実行: docker exec -i gto-db psql -U gto_user -d gto_db < 21b_analysis_hands_extend.sql

-- analysis_hands: hand_id（DB内のhands.hand_idへのリンク）
ALTER TABLE analysis_hands ADD COLUMN IF NOT EXISTS hand_id VARCHAR(200);

-- analysis_hands: B4フィールド（GTO統計精度向上）
ALTER TABLE analysis_hands ADD COLUMN IF NOT EXISTS bb_size NUMERIC;
ALTER TABLE analysis_hands ADD COLUMN IF NOT EXISTS pot_size_bb NUMERIC;
ALTER TABLE analysis_hands ADD COLUMN IF NOT EXISTS street_reached VARCHAR(10);

-- analyses: hand_ids（snapshotなしで復元可能にするためのhand_idリスト）
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS hand_ids JSONB DEFAULT '[]'::jsonb;

-- classified_snapshot は廃止予定（B5）。今は NULL 許容のまま維持。

-- 確認
\d analysis_hands
\d analyses
