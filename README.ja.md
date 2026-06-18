# 朔 — SAKU

[English](README.md) | 日本語

> **S**elf-**A**dapting **K**nowledge **U**nit

朔（さく）— 新月。何もない空から始まり、満ちて、欠けて、また始まる。

ローカルLLMで動く、自律成長型エージェントフレームワーク。

---

## What is SAKU?

チャットに答えるだけのツールではありません。SAKUは：

- **記録する** — ジャーナル・独り言・学んだ教訓をMarkdownファイルに蓄積する
- **振り返る** — 毎晩その日を総括し、翌日の計画を自分で立てる
- **調べる** — 自律的にWebを検索し、コードを書いて実験する
- **話しかける** — ユーザーを待つだけでなく、自分から会話を始める
- **成長する** — 経験を重ね、自己モデル（`meta.md`）を更新し続ける

エージェントの人格は `genome.md` に定義します。**ユーザー自身が書きます。**

---

## Why SAKU?

| 観点 | 他のフレームワーク | SAKU |
|---|---|---|
| 対象 | タスク実行パイプライン | 1つの人格を持つ個体 |
| 記憶 | ベクターDB / API | プレーンなMarkdownファイル |
| 動作 | 呼び出した時だけ | バックグラウンドで常時稼働 |
| environment | クラウド前提が多い | 完全ローカル（商用APIも可） |
| 拡張 | 設定ファイルやデコレータ | Pythonファイルを置くだけ |

ジャーナルや思考ログが普通のMarkdownファイルなので、好きなエディタで読めて、Gitで管理できます。

---

## Quick Start

```bash
# 0. ローカルLLMサーバーを起動（別ターミナル）
llama-server -m ~/models/your-model.gguf --host 127.0.0.1 --port 8080

# 1. リポジトリを取得
git clone https://github.com/omohikane/saku
cd saku

# 2. 設定ファイル
cp config.example.toml config.toml
# 必要に応じて [llm] api_url を編集

# 3. エージェントの人格定義
cp identity/genome.template.md identity/genome.md
cp memory/meta.template.md memory/meta.md
# identity/genome.md の {{...}} プレースホルダを編集
# 実装例: identity/examples/saku.md を参照

# 4. インタラクティブモードで起動
cd src && python saku_core.py

# 5. バックグラウンドデーモンとして起動（自律モード）
cd src && nohup python daemon.py > ../saku.log 2>&1 &
```

詳細なセットアップは [docs/SETUP.md](docs/SETUP.md) を参照してください。

---

## Defining Your Agent

SAKUの本体は `identity/genome.md`。ここにエージェントの人格を書きます。

### 1. テンプレートから開始

```bash
cp identity/genome.template.md identity/genome.md
```

`{{AGENT_NAME}}` `{{OWNER_NAME}}` などのプレースホルダを書き換えていきます。

### 2. 最低限決めること

- **名前** — エージェントの呼び名
- **本質** — 何のための存在か
- **価値観** — 何を大切にするか
- **文体** — どう話すか
- **禁止事項** — 何をしないか

### 3. 実装例を参考にする

`identity/examples/saku.md` に1つの実装例があります。
朔（Saku）というエージェントの定義です。コピーして名前だけ変えて使うのも可。

### 4. genome.md は個人ファイル

`.gitignore` で除外されています。あなたのエージェントの人格はあなただけのものです。

---

## Architecture

```
identity/
  genome.template.md   # 雛形（プレースホルダ入り、公開）
  genome.md            # ユーザーが書く実体（.gitignore済み）
  examples/
    saku.md            # 実装例（朔）

src/
  saku_core.py         # エージェントエンジン（LLM呼び出し・ツール実行・プロンプト構築）
  daemon.py            # バックグラウンドプロセス（ポーリング・自律ティック・夜間振り返り）
  reflect.py           # 夜間の日次サマリーと自己モデル更新
  tools/               # 拡張可能なツールプラグイン（ファイルを置くだけで有効）

memory/                # エージェントの記憶（プレーンなMarkdown）
  meta.md              # 自己モデル（.gitignore済み）
  journal/             # 日記・行動ログ
  monologue/           # 独り言・内省
  principles/          # 学んだ教訓
  drafts/              # 作業中のドキュメント
  skills/              # 獲得したスキル定義
  children/            # 子AIの定義
  study/               # コード実験サンドボックス

config.toml            # ユーザー設定（.gitignore済み）
config.example.toml    # 設定例（公開）
```

> **メモリストアについて**: SAKUはプレーンなMarkdownファイルを記憶として使います。
> Obsidianはその一例。`memory/` に相当する場所であれば、通常のディレクトリでも
> クラウド同期フォルダでも動作します。詳細は [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

---

## Daemon の動作

| 間隔 | 動作 | 状態 |
| ------ | ---------------------------------------------- | --------- |
| 5秒 | `chat.md` の新メッセージを確認 | ✅ 実装済 |
| 30分 | 自律ティック: Web調査・独り言・コード実験 | ✅ 実装済 |
| 1時間 | `00_Inbox/` フォルダのスキャン（Obsidian連携） | ✅ 実装済 |
| 8時間 | SAKUからの自発的な会話開始 | ✅ 実装済 |
| 毎日 02:00 | 夜間振り返り: その日を総括し翌日を計画 | ✅ 実装済 |

すべての間隔は `config.toml` の `[daemon]` セクションで変更できます。

---

## Conversation Interface（`chat.md`）

ターミナルではなく、Markdownファイルで会話できます：

1. `memory/chat.md` をエディタやObsidianで開く
2. メッセージを書いて、末尾に `>` を付けて保存
3. daemonが検知して自動返信

```markdown
今日の調子はどう？ >
```

`>` が送信トリガーです。自動保存による誤送信を防ぎます。

---

## Extending with Tools

`src/tools/` に Python ファイルを追加するだけで新しいツールを使えます：

```python
# src/tools/my_tool.py
from pathlib import Path

def run(base: Path, path: str, body: str = "") -> str:
    # body = [[MY_TOOL]] と [[END]] の間の内容
    return "[OK] result"
```

エージェントはこう呼び出します：

```
[[MY_TOOL]]
input here
[[END]]
```

登録不要。`src/tools/` に置くだけで動的にロードされます。
詳細は [docs/TOOLS.md](docs/TOOLS.md)。

---

## Roadmap

| Phase | 名前 | 内容 | 状態 |
| ----- | ---- | ----------------------------------- | ------- |
| 0 | 書く | ノート整理、ドキュメント執筆 | ✅ Done |
| 1 | 知る | Webリサーチ、自律学習ループ | ✅ Done |
| 2 | 守る | ホームネットワーク監視、異常検知 | planned |
| 3 | 繋ぐ | 外部サービス連携、API統合 | planned |
| 4 | 生む | 特化型子AI（Sub-Agent）の生成・管理 | planned |

### Ideas / 今後の方向性

- [ ] Memory store の抽象化レイヤー（SQLite、Vector DB）
- [ ] `chat.md` の代わりになるWeb UI
- [ ] ツールのコミュニティレジストリ
- [ ] マルチエージェント対応（複数の genome インスタンス）
- [ ] 長期記憶の圧縮・要約メカニズム

---

## What SAKU is NOT

- クラウドサービスではありません。すべてローカルで動きます
- Obsidian専用ではありません。記憶ストアの一例として使っているだけです
- 特定のLLMに依存しません。OpenAI互換APIが使えれば動きます
- プロダクション品質ではありません（アルファ段階）

---

## Documentation

- [docs/SETUP.md](docs/SETUP.md) — 詳細なセットアップ手順
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 構成・データフロー
- [docs/TOOLS.md](docs/TOOLS.md) — ツール拡張ガイド
- [docs/DAEMON.md](docs/DAEMON.md) — daemon動作の詳細
- [CONTRIBUTING.md](CONTRIBUTING.md) — コントリビュート方法

---

## License

MIT — see [LICENSE](LICENSE)
