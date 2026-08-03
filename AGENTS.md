# AGENTS.md — SAKU プロジェクト固有ルール

グローバルルール（`~/.config/opencode/AGENTS.md`）に加えて、このリポジトリに適用するルール。

## テスト

- **コード変更後は必ずテストを実行する**:
  ```bash
  cd tests && for t in test_*.py; do uv run python "$t"; done
  ```
  （`test_mcp_server.py` はstdioサーバのフィクスチャのため除外。サブディレクトリ `tests/security/` も含める）
- CI（GitHub Actions）でも全テストが実行される。

## 個人データ保護

以下のファイル・ディレクトリは**ユーザーの個人データ**であり、明示的な指示がない限り**変更しない**:

- `memory/`（Obsidian vault: journal / monologue / principles / skills / wiki / MEMORY.md / meta.md など）
- `config.toml`（`.gitignore` 済みのローカル設定）
- `identity/genome.md`（個人の人格定義。マスターはvault側）

## コミット規約

リポジトリの既存ログに合わせ、**タイプ接頭辞**を使う:

- `feat:` 新機能 / `fix:` バグ修正 / `docs:` ドキュメント / `refactor:` リファクタ（挙動不変） / `test:` テスト / `chore:` 雑務 / `ci:` CI

1コミット=1論理変更。挙動変更とリファクタは分ける。

## ブランチ運用

- 作業ブランチは **`dev`**。`main` はリリース版。
- 非自明なタスクはtopicブランチ（`feat/*` `refactor/*`）で作業し、テスト通過後にdevへ統合。
- `main` への反映はPR経由（ユーザー指示時のみ）。

## 実行環境

- venv（uv）で実行: `uv run python -m saku.cli <cmd>`
- LLM設定は `config.toml` の `[llm]`（litellm等）。勝手に変更しない。
