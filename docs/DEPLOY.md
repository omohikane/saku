# DEPLOY — 起動とデプロイ

SAKUの起動方法と、常駐化（systemd）・専用VMでの運用方法。

## 1. 手動起動

リポジトリルートで実行:

```bash
# Web UI + 自動ループ（daemon）を一緒に起動（推奨）
python -m saku.ui

# オプション
python -m saku.cli chat      # ターミナル対話のみ
python -m saku.cli daemon    # daemonのみ
python -m saku.ui --no-daemon  # Web UIのみ（自動ループなし）
```

ブラウザで http://127.0.0.1:8787 を開く。

## 2. メモリ（vault）の設定

`config.toml` の `[memory] root` でメモリの場所を指定します:

```toml
[memory]
root = "memory"                          # リポジトリ内（デフォルト）
root = "/path/to/vault/_saku/memory"     # 絶対パス（Obsidian vault等）
root = "${SAKU_MEMORY_ROOT}"             # 環境変数（マシン間で可搬）
```

環境変数は `${VAR}` 形式で展開されます。未設定の変数はそのまま残ります。

初回は構造を作成します:

```bash
python -m saku.cli setup            # 設定済みの [memory] root に作成
python -m saku.cli setup /path/to/vault/_saku/memory   # 明示指定
```

## 3. systemd での常駐（推奨）

単一プロセスで Web UI + daemon を起動するユニットを同梱しています
(`packaging/saku.service`)。

```bash
# 前提: リポジトリを /opt/saku に配置し、venv を作成
cd /opt/saku
python3 -m venv .venv
.venv/bin/pip install -e .

# サービスファイルを編集（WorkingDirectory / SAKU_MEMORY_ROOT / User 等）
sudo cp packaging/saku.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now saku

# 確認
journalctl -u saku -f
systemctl status saku
```

## 4. 専用VMでの運用

1. VM（Ubuntu等）に Python 3.11+ と uv を導入
2. リポジトリを clone: `git clone ... saku && cd saku`
3. `python3 -m venv .venv && .venv/bin/pip install -e .`
4. `cp config.example.toml config.toml` して `[llm]` `[memory] root` を設定
5. `python -m saku.cli setup` でメモリ構造を作成
6. 上記 systemd ユニットで常駐化
7. 必要なら `[ui] host = "0.0.0.0"` にしてネットワーク公開（要セキュリティ考慮）

### ネットワーク公開時の注意

- 既定は `127.0.0.1`（ローカルのみ）
- 外部公開する場合はリバースプロキシ + TLS + 認証を推奨（現在のWeb UIには認証なし）
- MCPサーバ公開（Phase C）ではトークン認証を実装予定

## 5. データのバックアップ

メモリはすべて Markdown ファイルのため、git や Obsidian Sync 等でそのまま
バックアップ・同期できます。`memory/state/`（ログ・状態）は機械生成です。
