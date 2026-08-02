"""コンテキスト管理。

VRAM/RAM制約を前提に、エージェントが使うコンテキストを「小さく保つ」ための
ヘルパー群。作業予算は ``[llm.instances.*]`` ごとに設定し、インスタンスの
``working_budget_tokens`` を基準にコンパクションを判断する。

- トークン推定（文字数近似。トークナイザ非依存）
- 古いツール結果の縮小/削除（pruning。要約不要）
- 予算超過時の履歴圧縮（コンパクション）
"""

from .config import ContextConfig


def estimate_tokens(text: str, chars_per_token: int = 3) -> int:
    """トークン数の近似推定。

    モデルやトークナイザに依存しない簡易近似。日本語は文字≈トークンに近いため、
    ``chars_per_token=3`` は英字中心でも日本語でもある程度の安全側マージンを持つ。
    """
    return max(1, len(text) // chars_per_token)


def total_tokens(history: list[dict], chars_per_token: int = 3) -> int:
    return sum(estimate_tokens(str(m.get("content", "")), chars_per_token) for m in history)


def is_tool_result_message(msg: dict) -> bool:
    """履歴内の「ツール結果」メッセージ（ユーザーロールの tool results ブロック）か判定。"""
    return msg.get("role") == "user" and str(msg.get("content", "")).startswith("[system] tool results:")


def trim_old_tool_results(history: list[dict], ctx: ContextConfig) -> list[dict]:
    """古いツール結果を縮小する。直近 ``keep_full`` 件以外は指定文字数へ切り詰める。

    ツール結果は要約なしで縮小できる（OpenClaw の session pruning と同じ発想）。
    """
    keep_full = 2
    seen = 0
    out: list[dict] = []
    for msg in reversed(history):
        if is_tool_result_message(msg):
            seen += 1
            if seen > keep_full:
                content = str(msg["content"])
                if len(content) > ctx.max_tool_result_chars:
                    msg = dict(msg)
                    msg["content"] = content[: ctx.max_tool_result_chars] + "\n\n[... tool output truncated to save context ...]"
        out.append(msg)
    out.reverse()
    return out


def needs_compaction(history: list[dict], budget_tokens: int, ctx: ContextConfig, chars_per_token: int = 3) -> bool:
    """作業予算に対してコンパクションが必要か判定する。"""
    if budget_tokens <= 0:
        return False
    return total_tokens(history, chars_per_token) > int(budget_tokens * ctx.compaction_trigger)


def truncate_history(history: list[dict], keep_recent_tokens: int, chars_per_token: int = 3) -> tuple[list[dict], int]:
    """システムプロンプト（index 0）を保持しつつ、末尾から keep_recent_tokens 分だけ残す。

    戻り値: (新しい履歴, 破棄した文字数)
    """
    if not history:
        return history, 0
    system = history[0]
    tail = list(history[1:])

    kept: list[dict] = []
    used = 0
    limit = keep_recent_tokens * chars_per_token
    for msg in reversed(tail):
        used += len(str(msg.get("content", "")))
        kept.append(msg)
        if used >= limit:
            break
    kept.reverse()

    kept_chars = sum(len(str(m.get("content", ""))) for m in kept)
    total_chars = sum(len(str(m.get("content", ""))) for m in tail)
    return [system] + kept, total_chars - kept_chars
