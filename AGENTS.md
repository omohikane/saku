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

## Issue運用（個人開発でも採用）

バグ発見・調査・設計検討は **GitHub Issue で起票**してから対応する:

- バグ: 再現手順・エラー内容をissueに記録 → 修正ブランチ（`fix/*`）で対応 → `Closes #N` 付きPRでdevへ
- 調査/設計（questionラベル）: 調査結果と次の予定をissueコメントに残す（再発時の再調査を防ぐ）
- 自明な1行修正やtypo修正はissueなしで直接コミットしてよい
- pushはユーザー手動（deny設定）。PR作成もユーザーか、明示指示時のみ

## 実行環境

- venv（uv）で実行: `uv run python -m saku.cli <cmd>`
- LLM設定は `config.toml` の `[llm]`（litellm等）。勝手に変更しない。

## 開発方針

- **ハードコード排除**: パス・閾値・保存先等の固定値は避け、 `config.toml`（`[wiki] root` `[memory] inbox_dir` `[plugins] root` 等）と対話（ツール引数）で可変にする。初回から可変実装を原則とする（`docs/DESIGN.md` 8 参照）。
