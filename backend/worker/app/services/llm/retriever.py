from __future__ import annotations

import os
from pathlib import Path
import json
import logging
from typing import Any, Dict, List, Optional

_log = logging.getLogger(__name__)


def _knowledge_base() -> Path:
    # 例: backend/worker/data/knowledge
    return Path(os.getenv("KNOWLEDGE_DIR", "backend/worker/data/knowledge")).resolve()


def _value_from_ref(ref: Any, key: str, default: Any = None) -> Any:
    if isinstance(ref, dict):
        return ref.get(key, default)
    return getattr(ref, key, default)


def _find_md_by_spot_id(lang: str, spot_id: str) -> List[Dict]:
    """
    knowledge/{lang}/faci_spot/{spot_id}.md を探索。
    """
    base = _knowledge_base() / lang / "faci_spot"
    if not base.exists():
        return []

    target_file = base / f"{spot_id}.md"
    matches: List[Dict] = []
    if target_file.is_file():
        try:
            txt = target_file.read_text(encoding="utf-8")
            matches.append({"text": txt, "source": str(target_file)})
        except Exception as e:
            _log.warning(f"Failed to read {target_file}: {e}")
    return matches


def _load_md_by_spot_id(spot_id: str, lang: str) -> Optional[Dict[str, str]]:
    if not spot_id:
        return None

    docs = _find_md_by_spot_id(lang, spot_id)
    if not docs:
        return None

    doc = docs[0]
    text = doc.get("text")
    if not text:
        return None

    source = doc.get("source")
    base = _knowledge_base()
    if source:
        try:
            rel = Path(source).resolve().relative_to(base)
            source = str(rel)
        except Exception:
            source = str(source)
    else:
        source = f"{lang}/faci_spot/{spot_id}.md"

    return {"text": text, "source": source}


def retrieve_context(spot_ref, lang: str) -> list[dict]:
    """
    spot_id に紐づくMDがあればそれを使い、なければ description を使う。
    """
    ctx: list[dict] = []

    # 1) spot_id を最優先で追加
    spot_id = _value_from_ref(spot_ref, "spot_id")
    if spot_id:
        md_doc = _load_md_by_spot_id(spot_id, lang)
        if md_doc:
            ctx.append(md_doc)

    # spot_id でドキュメントが見つからなかった場合、description を使う
    if not ctx:
        desc = _value_from_ref(spot_ref, "description")
        if desc:
            if isinstance(desc, (dict, list)):
                try:
                    desc_text = json.dumps(desc, ensure_ascii=False)
                except Exception:
                    desc_text = str(desc)
            else:
                desc_text = str(desc)
            if desc_text:
                ctx.append({"text": desc_text, "source": "spot.description"})

    return ctx
