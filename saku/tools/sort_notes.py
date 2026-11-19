"""SORT_NOTES tool: propose (or, if allowed, apply) clipping classification.

Usage:
- Propose: [[SORT_NOTES]]
- Limit the proposal: [[SORT_NOTES limit="10"]]
- Apply the latest proposal: [[SORT_NOTES apply="true"]]
  (only when ``[sort] allow_agent_apply = true``; refused otherwise)

Proposing writes nothing outside ``state/sort/``: the clippings stay put. Applying
moves files, so it is off by default even though the Owner can run
``saku sort --apply`` by hand.
"""

from pathlib import Path


def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    from saku import config as cfg_mod

    cfg, cbase = cfg_mod.load_config()
    scfg = cfg_mod.load_sort_config(cfg)
    apply = str(kwargs.get("apply", "")).lower() in ("1", "true", "yes")

    from saku.sort import (
        apply_proposal,
        build_proposal,
        latest_proposal,
        load_proposal,
        mark_applied,
        resolve_paths,
        save_proposal,
    )

    paths = resolve_paths(cfg, cbase, base)

    if apply:
        if not scfg.allow_agent_apply:
            return (
                "[ERROR] SORT_NOTES apply is disabled "
                "([sort] allow_agent_apply = false)"
            )
        proposal_path = latest_proposal(base)
        if proposal_path is None:
            return "[ERROR] no saved proposal; run SORT_NOTES without apply first"
        proposal = load_proposal(proposal_path)
        report = apply_proposal(base, proposal, review_below=scfg.review_below)
        if report["moved"]:
            mark_applied(base, proposal)
        lines = [f"[SORT_NOTES] applied {len(report['moved'])} file(s)"]
        lines += [f"  moved: {name} -> {dest}" for name, dest in report["moved"]]
        lines += [f"  skip: {name} ({why})" for name, why in report["skipped"]]
        return "\n".join(lines)

    try:
        limit = int(kwargs.get("limit", 0) or 0)
    except (TypeError, ValueError):
        limit = 0
    proposal = build_proposal(
        base,
        paths,
        limit=limit or scfg.max_files,
        max_chars=scfg.max_chars,
        review_below=scfg.review_below,
    )
    if not proposal.items:
        return "[SORT_NOTES] no clippings to classify"
    saved = save_proposal(base, proposal)
    lines = [f"[SORT_NOTES] proposal saved: {saved.name} (nothing moved)"]
    for item in proposal.items:
        lines.append(
            f"  {item['confidence']:.2f}  {item['folder']:<12} {item['file']}"
        )
    lines.append("Owner can apply it with: saku sort --apply")
    return "\n".join(lines)
