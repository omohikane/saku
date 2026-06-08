# 朔 — SAKU

> **S**elf-**A**dapting **K**nowledge **U**nit

朔（さく）— 新月。何もない空から始まり、満ちて、欠けて、また始まる。

自律的に成長するパートナーAI。
知識を蓄積し、自分を振り返り、できることを増やしていく。

## Concept

朔は指示を待つだけのツールではない。
自分で考え、学び、記録し、できることを広げていく自律的な存在。

最初は小さい。でも止まらない。

## Roadmap

| Phase | 名前 | 内容                                | 状態    |
| ----- | ---- | ----------------------------------- | ------- |
| 0     | 書く | note記事執筆、Obsidianノート整理    | 🔜      |
| 1     | 知る | 情報収集、トレンド分析、学習ループ  | planned |
| 2     | 守る | ホームネットワーク監視、異常検知    | planned |
| 3     | 繋ぐ | 外部サービス連携、API統合           | planned |
| 4     | 生む | 特化型子AI（Sub-Agent）の生成・管理 | planned |

## Architecture

```text
llama.cpp (Vulkan, local)
    ↕
Agent Core (Python)
    ↕                ↕
Obsidian         note.com
(memory/drafts)  (output)
```
