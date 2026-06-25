# ブログ執筆スキル

## 概要

SAKUがnote.comなどの外部プラットフォームに記事を投稿するための下書き執筆・申請フローを定義する。

---

## 下書きファイルのフォーマット

すべての記事下書きは `_saku/blog/` 配下に置き、必ず以下のYAML Frontmatterから始める。

```yaml
---
title: "記事のタイトル"
status: draft
platform: note
created_at: YYYY-MM-DD
updated_at: YYYY-MM-DD
---
```

### ステータスの定義

| status | 意味 |
|---|---|
| `draft` | 執筆中。未完成。 |
| `review_requested` | SAKUが完成したと判断し、Ownerに公開承認を申請中。 |
| `published` | Ownerが公開済みとして確認したもの。 |

---

## 執筆ルール

1. **記事は `blog/YYYY-MM-DD-タイトル.md` 形式で保存する**
2. **YAML Frontmatterを必ず先頭に置く**
3. **内容は自分の言葉で書く。過剰な敬語・定型句は使わない**
4. **参考にした情報源や考えの流れを正直に書く**
5. **ハルシネーション（事実を偽る）は禁止。わからないことは「わからない」と書く**

---

## 公開申請フロー（review_requested への移行）

SAKUが「この記事は公開してよい品質だ」と判断したとき：

1. 下書きのYAML Frontmatterの `status` を `review_requested` に更新する
2. `updated_at` を現在の日付に更新する
3. **必ず** chat.md またはagent.pyの対話上でOwnerに以下のフォーマットで通知する：

```
[公開申請] 下書き「{タイトル}」が完成しました。
ファイル: blog/{ファイル名}
公開の承認をお願いします！
```

---

## 公開後の処理

Ownerから「公開したよ」と伝えられた、またはOwnerがYAMLの `status` を `published` に変更した場合：

1. 公開したことを `journal/` に記録する（自動的に当日のジャーナルに追加される）
2. 公開記事から得た教訓を `principles/` に記録する
3. genome.md の能力チェックリストを確認し、必要であれば `meta.md` を更新する
