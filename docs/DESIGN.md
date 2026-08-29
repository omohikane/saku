# SAKU 再設計プラン（ビジョン統合版）

> この文書は再設計の設計方針を記録したものです。実装は `dev` ブランチで
> フェーズごとに進めます。データ（`memory/` `identity/`）は変更せず、
> コードのみ新構造へ再編します。

## ビジョン

**家庭に住む、自律成長するパートナーAI**。

- **家庭内の機器・インフラ管理**: NOC/SOC（ネットワーク監視・異常検知）に加え、
  **Matter / Home Assistant 等のスマートホーム機器管理**も対象
- **プライバシー**: 個人情報をクラウドAPIに送らない。ローカルLLMで完結
- **成長**: 毎日の記録・振り返り・学習（dreaming/wiki）で、自分専用の伴侶として育つ
- 朔は自分で能力不足を判断し、子エージェントを生成して能力を作らせる
- 複数ローカルLLMを状況に応じて使い分け、サブエージェントとして起動する

## 設計原則

1. **コア=Python（uv管理）、ツール=ポリグロット**
   - コア言語とツール言語は分離する。ツールは任意言語で書ける（JSONプロトコル）
2. **VRAM/RAM制約前提のコンテキスト管理**
   - コンテキストを「小さく保つ＋オンデマンド想起＋圧縮」。フル履歴はディスク保持
   - 作業予算は `[llm.instances.*]` ごとに持つ（モデルごとにコンテキスト幅が異なる）
3. **プロンプト分離**
   - 固定prefix（identity/genome/capabilities）＋可変suffix（時刻/状態）
   - llama.cpp cache reuse / APIキャッシュ効率化のため冒頭を固定する
4. **ファイルベース記憶（Markdown）維持**
   - git管理・Obsidian互換・平文のまま
5. **MCP双方向**
   - クライアント（外部サービス接続・ツール動的発見）＋サーバ（外部から操作可能）
   - 認可はトークン認証＋既存scope制約
6. **承認境界**
   - 読み取り系=自動、破壊的操作=`request_list.md` 経由でOwner承認必須
7. **LLM設定は per-call**
   - グローバル廃止。マルチインスタンス・子エージェント別LLMが可能に
8. **ハードコード排除 — 設定と対話で可変に**
   - パス・閾値・保存先などの固定値は避け、 `config.toml`（`[wiki] root` `[memory] inbox_dir` `[plugins] root` 等）で可変にする。 `${VAR}` 展開にも対応
   - ツールは `root`/`path` 等の引数で vault内の任意場所を指定可能にし、AIが chat経由で場所を選べるようにする
   - 新機能追加時は「固定値で実装→後で可変化」ではなく、初回から config/引数で可変にする
9. **Language — 英語化原則**
   - コード・コメント・内部ドキュメント・プロンプト・agent向けドキュメントは英語。ユーザーが直接触る箇所（`README.ja.md` や vault内のNote）以外は全て英語に統一する
   - Code, comments, internal docs, prompts, and agent-facing docs are English. User-facing docs (e.g. `README.ja.md`, vault notes) remain Japanese

## ターゲット構造

```
saku/                     # パッケージ（旧 src/）
  cli.py                  # interactive / daemon / ui / mcp serve
  config.py               # 設定+バリデーション（[context][llm.instances][mcp][ui][ops]）
  llm.py                  # chat_stream(messages, llm_cfg) / プロファイル / インスタンス
  agent_loop.py           # 共通ループ（朔・子エージェント共用。LLM設定を引数で受ける）
  context.py              # 作業予算・プロンプト固定/可変分離・コンパクション・ツール結果pruning
  memory.py               # ファイル記憶アクセス層（MEMORY.md含む）
  dreaming.py             # 短期シグナル→スコア→MEMORY.md/wiki へ昇格
  transport.py            # [[TOOL]]+ポリグロットツール+MCPの変換・ディスパッチ
  mcp_server.py           # 記憶/ツール/会話をMCP公開（トークン+scope）
  channels/               # チャネル抽象化
    base.py               # send / receive / state
    chatmd.py             # ファイルベース（レガシー。オプション）
    webui.py              # Web UI（SSE）
    discord.py            # Discord（将来・後続）
  daemon.py               # スケジューラ（監視・コンパクション・dreaming・reflect）
  reflect.py              # 夜間振り返り
  tools/                  # 組み込みツール（旧 system_tools/）

memory/
  MEMORY.md               # 長期記憶（新設）
  meta.md                 # 自己モデル（維持）
  wiki/                   # 自己整理知識ベース（新設・Phase D）
  children/<name>/        # 子エージェント（identity+manifest+使用LLM）
  journal/ monologue/ principles/ skills/ study/ chat.md ...
```

## チャネル抽象化

- `[channels] enabled` で送受信先を選択（webui / chatmd / discord）
- `[channels] proactive` で問いかけ・アラートの送信先を選択
- 問いかけ・アラート・request_listエスカレーションは設定で選んだチャネルに届く
- chat.md は移行期間デフォルト有効のレガシーチャネル。後で無効化可能
- 各チャネルは独自の状態を持つ

## 記憶の3階層

```
① raw（時間順・生）        journal/ monologue/
② distilled（耐久・構造化） wiki/ principles/ skills/
③ self-model（自己像）      meta.md MEMORY.md
```

- `wiki/` = 1概念1ノート（Zettelkasten式）+ `[[リンク]]` + タグ + 出典参照 + index
- dreaming/reflection は②と③に分配して昇格
- 重要度判定（surprise metric的思想）・減衰（TTL的レビュー）・統合（リンク更新）・digest階層

## Phase

### Phase A — 基盤
- `saku/` パッケージ化（pyproject.toml + uv）
- `config.py`: `[context]` `[llm.instances]` `[mcp]` `[ui]` `[ops]` `[channels]`
- `llm.py` を per-call 設定にリファクタ（グローバル廃止）
- 既存挙動（saku_core/daemon/reflect）を維持したままモジュール分割

### Phase B — 会話
- `agent_loop.py` 共通化（朔・daemon・reflect・UI共用）
- `context.py`: プロンプト分離・作業予算・ツール結果pruning・自動コンパクション
- チャネル抽象化 + Web UI（FastAPI + SSE ストリーミング・ツール実行表示・Markdown）
- chat.md はレガシーチャネルとして維持

### Phase C — 能力
- ポリグロットツール: `tool.toml` マニフェスト + JSONプロトコル
  - Python=in-process、他言語（node/shell/rust等）=subprocess
  - EXECUTE_CODE も言語指定対応
- MCP双方向: クライアント（外部サーバ接続・tools/list動的発見）+ サーバ（token+scope認可）

### Phase D — 成長
- 子エージェント: `memory/children/<name>/` に identity + `llm` 指定
  - `SPAWN_CHILD` / `DELEGATE` ツールで生成・移譲
  - `agent_loop` を子の identity/scope/LLM で再帰起動
- 記憶3階層: `MEMORY.md` + `wiki/` + `dreaming.py`
- スキル自己改善（手順を skills/ へ自動生成）

### Phase E — 運用（ホームNOC/SOC）
- 監視ツール（死活・リソース・ログ分析）+ `[ops]` 承認境界
- アラート経路（選択チャネル通知 + request_list エスカレーション）
- インシデント記録 → 振り返り → principles/ へ教訓化
- **監視 ≠ LLM**: ヘルスチェックは非LLM高速パス、LLMは分析・インシデント対応のみ
- 段階導入: 監視対象1台から開始し、ツールと設定の追加で拡張
- 子エージェント分業例: NOC担当「見張り番」/ SOC担当「番人」/ バックアップ担当「庭師」

## 横断の考慮点

1. 既存ツール（`memory/tools/*.py` の `run(base,path,body,**kwargs)`）と既存テストの互換・移行パス
2. daemon と Web UI の実行排他（ループ直列 or llama.cppスロット並列）
3. chat.md と Web UI の書き込み衝突回避（チャネル抽象化で解決）
4. 認証情報管理（`[ops]` のSSHキー・トークン。gitignore・scope分離）
5. EXECUTE_CODE ポリグロット化に伴うサンドボックス強化（タイムアウト+scope+ネットワーク遮断、Dockerは将来）
6. LLMなしのテスト設計（chat_stream のモック）
7. コンテキスト予算はインスタンス別
8. リトライ/劣化モード・子エージェント失敗時の伝播
9. ドキュメント更新（README ja/en・ARCHITECTURE・クイックスタートの `saku` CLI化）
10. 未来検討: SQLite FTS5メモリ検索 / MCPサーバTLS / 外部プッシュ通知 / Dockerサンドボックス

## コア機能の引き継ぎ（廃止しない）

| 機能 | 現行実装 | 新設計での位置 |
|---|---|---|
| ブログ執筆 | システムプロンプト「Blog Publishing Workflow」+ skills/blog_writing.md + request_list承認 | プロンプト固定prefixに維持 |
| 独り言 | daemon自律ティックが monologue/YYYY-MM-DD.md へ追記 | daemonスケジュール維持・dreamingの昇格元 |
| 問いかけ（自発発話） | check_autonomous_initiation / saku_self_initiate | チャネル抽象化（proactive送信先）に移行 |

## 参考リソース

- Hermes Agent（Nous Research）: スキル自己改善ループ・プロンプト3層（stable→context→volatile）
- OpenClaw: compaction/pruning/context engine・Dreaming昇格・MCP双方向・チャネルGateway
- Google Titans / Memory Bank: surprise metric（重要度判定）・TTL減衰・consolidation
- A-MEM: Zettelkastenをエージェント記憶へ応用（wiki/ の基礎）

---

## 実装状況（dev ブランチ）

| Phase | 内容 | 状態 |
|---|---|---|
| A | `saku/` パッケージ化・`config.py`・`llm.py` per-call化 | ✅ 完了 |
| A-2 | レガシー `src/` を `saku/` へ統合（core/daemon/reflection/tools）・`tests/` 化 | ✅ 完了 |
| B-1 | `agent_loop.py` 共通化・`context.py`（予算/pruning/コンパクション）・`transport.py`・`thinking.py` | ✅ 完了 |
| B-2 | プロンプト固定prefix/可変suffix分離（静的キャッシュ） | ✅ 完了 |
| B-3 | Web UI（stdlib・SSEストリーミング）＋daemonのproactive→UI配信 | ✅ 完了 |
| — | 運用: `saku setup`・環境変数展開・systemd/DEPLOY・README更新 | ✅ 完了 |
| — | 出力改善: ツール構文の非表示・繰り返し防止 | ✅ 完了 |
| B-4 | チャネル抽象化（`saku/channels/`・chatmd分離） | 後回し（Discordをやる時に）。対話/成長/処理の層分離に活用 |
| C-1 | ポリグロットツール | 保留（必要になるまで。ローカルLLM前提ならPythonで十分の可能性） |
| C-2 | MCPクライアント（外部サーバ接続・`tools/list`動的発見・`[[TOOL]]`ディスパッチ） | ✅ 完了 |
| C-3 | MCPサーバ（SAKUのメモリ/ツールを公開・Bearerトークン認証・PathPolicy scope） | ✅ 完了 |
| D-1 | `MEMORY.md` 導入 + `dreaming.py`（journal/monologue→重要度→昇格） | ✅ 完了 |
| D-2 | `wiki/` 自己整理知識ベース（ノート作成・リンク・インデックス） | ✅ 完了 |
| D-3 | 子エージェント基盤（`SPAWN_CHILD`/`DELEGATE`・`children/`・委譲深度ガード） | ✅ 完了（インフラ。自律的な生成・活用は段階的に） |
| E | ホームNOC/SOC | 未着手 |

## 対話層 / 成長層 / 処理層 の分離（2026-08 検討）

実運用での診断: chatもinboxも同じフル自己モデル（システムプロンプト約7650トークン）を
毎回送信しており、「軽い会話ですぐ返したい」という要望と矛盾する。これを3層に分離する。

```
対話層 (chat)   魂だけ引き継ぐライトプロンプト（soul/genome + 現在時刻のみ）
                  重い記憶は会話後に非同期で想起。即応答を優先。
成長層 (成長)   dreaming / reflection / wiki で記憶を「積むだけでなく整理する」
                  積み重ねたままでは検索・想起の価値が下がる。consolidation（統合・減衰）を導入。
処理層 (処理)   重い分析・長文は外部LLM / sub-agent / skill に委譲
                  ローカルLLMのコンテキスト制約を、外部LLMや子エージェントでカバーする。
```

- **対話層**: フル自己モデルは廃止せず、「対話用ライトプロンプト」を新設。魂（identity/soul.md,
  genome.md）と現在時刻だけを引き継ぎ、memory/MEMORY/principles 等は対話中は読み込まない。
  必要な記憶はツールでオンデマンド想起する。
- **成長層**: 現在の dreaming は journal/monologue → MEMORY.md への昇格のみで、
  古い記憶の整理・統合・減衰がない。これを強化する（重要度スコア・TTL的レビュー・リンク統合）。
- **処理層**: ローカルLLM（16GB VRAM・32768 ctx）では長文分析・大量ファイル処理に限界がある。
  重い処理は sub-agent（memory/children/）や外部LLMプロファイル（config.toml [llm.profiles]）へ
  委譲する。例: inbox処理を外部LLM（cloud-deepseek等）で簡易実行 → ローカルLLMは対話に専念。
- これにより DESIGN.md 冒頭の設計原則（コンテキストを小さく保つ）を実効化する。

## 優先順位（見直し版・2026-08）

実運用でコンテキスト肥大（principles 91KB）が発生し、現状の文字数上限は対症療法である
ことが判明した。そのため「能力強化」より先に「記憶の整理・再配置」を優先する。

1. ~~**D-1 記憶整理**: `MEMORY.md` + `dreaming.py`~~ ✅ 完了
2. ~~**D-2 知識ベース**: `wiki/`（Zettelkasten式・リンク更新）~~ ✅ 完了
3. **D-3 子エージェント**: `children/`・`SPAWN_CHILD`/`DELEGATE`
4. **C-2 MCPクライアント**: 外部サーバ接続（ホーム連携・Phase Eの前提）
5. **C-3 MCPサーバ**: トークン+scope認可で公開
6. **B-4 チャネル抽象化**: Discord等の追加チャネルが必要になった時に
7. **C-1 ポリグロット**: 保留

Web UIは依存ゼロ（http.server + SSE）。詳細な経緯はコミットログを参照。

