# Longitudinal Evaluation

SAKU が実際に「自己成長」しているかを**時系列で**測るための評価基盤。

> "self-evolving" is easy to claim but hard to show.
> Snapshots let you compare across time and verify the agent is actually changing.

## 仕組み

定期的（例: 週1回）にスナップショットを取得し、時系列で比較します。

```bash
# 週1回（例: 毎週日曜）
uv run python evals/longitudinal/snapshot.py evals/longitudinal/snapshots/2026-08-10.json

# 2時点を比較
uv run python evals/longitudinal/compare.py \
  evals/longitudinal/snapshots/2026-08-03.json \
  evals/longitudinal/snapshots/2026-08-10.json
```

## 何を測るか

| 指標 | 何を意味するか |
|---|---|
| **MEMORY.md / meta.md のサイズ・項目数** | 長期記憶・自己モデルの成長（セクションごとの増減） |
| **principles / skills / wiki ノート数** | 学習した教訓・技能・知識ベースの蓄積 |
| **journal / monologue 数** | 活動量・記録の継続性 |
| **children / tools 数** | 子エージェント・自作ツールの拡張 |

## 成長の問い（この評価で検証したいこと）

- 1週間前より**同じ失敗が減ったか**（principlesの蓄積と行動変化）
- **記憶の精度**は上がったか・**誤記憶**は増えていないか
- **人格の一貫性**は保たれているか

上記はスナップショット（構造）だけでは直接測れないため、別途
`tests/persona/` `tests/memory/` `tests/autonomy/` の**LLMあり評価**で
定性的に検証します（計画中）。

## スナップショットをgit管理する

スナップショットは小さいJSONなので、`evals/longitudinal/snapshots/` を
git管理して履歴として残すと、成長の記録がそのまま残ります。
