import json
import re
import unicodedata
import difflib
from functools import lru_cache
from pathlib import Path
from typing import TypedDict, List, Dict, Any, Set, Tuple, Optional
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

# エージェント内の各モジュールをインポート
from . import db_client
from . import recommender
from . import vector_store_client
from backend.worker.llm_client import generate_text
from .prompts import (
    get_intent_parser_prompt,
    get_intent_parser_retry_prompt,
    get_profile_extractor_prompt,
    get_profile_question_prompt,
    get_response_generator_prompt,
)
from .profile_taxonomy import (
    TRAVEL_STYLE_CATEGORIES,
    TRAVEL_STYLE_SYNONYMS,
    TRAVEL_FREQUENCY_CATEGORIES,
    TRAVEL_FREQUENCY_SYNONYMS,
    PREFERRED_DESTINATION_CATEGORIES,
    PREFERRED_DESTINATION_COMPONENT_SYNONYMS,
)

# --- 1. State (状態) の定義 --- #
class AgentState(TypedDict):
    user_name: str
    language: str
    user_profile: dict
    itinerary: List[str]
    chat_history: List[dict]
    user_input: str
    parsed_intent: dict
    recommendations: list
    response_text: str
    awaiting_profile_fields: List[str]

# --- Helper utilities --- #

TRAD_TO_SIMPLIFIED_MAP = str.maketrans({
    "亞": "亚",
    "鳥": "鸟",
    "溫": "温",
    "氣": "气",
    "點": "点",
    "灣": "湾",
    "國": "国",
    "體": "体",
    "與": "与",
    "嶼": "屿",
    "館": "馆",
    "裏": "里",
    "廣": "广",
    "澤": "泽",
    "縣": "县",
    "臺": "台",
    "鄉": "乡",
    "學": "学",
    "戶": "户",
    "們": "们",
    "長": "长",
    "經": "经",
})

SIMPLIFIED_TO_TRAD_MAP = str.maketrans({v: k for k, v in {
    "亞": "亚",
    "鳥": "鸟",
    "溫": "温",
    "氣": "气",
    "點": "点",
    "灣": "湾",
    "國": "国",
    "體": "体",
    "與": "与",
    "嶼": "屿",
    "館": "馆",
    "裏": "里",
    "廣": "广",
    "澤": "泽",
    "縣": "县",
    "臺": "台",
    "鄉": "乡",
    "學": "学",
    "戶": "户",
    "們": "们",
    "長": "长",
    "經": "经",
}.items() if v != k})

ADDITIONAL_ALIASES: Dict[str, List[str]] = {
    "spot_001": ["Agariko Daio", "亞加里子大王", "阿伽利子大王", "阿伽里子大王"],
    "spot_003": ["Botsumeki Spring", "ボツメキ湧水", "ボツメキ水源", "ボツメキ湧泉", "菩次美氣湧泉", "Botumeki Spring"],
    "spot_005": ["鳥海湖", "鸟海湖", "Lake Chokai", "鳥海山湖"],
}

SPOT_ID_PATTERN = re.compile(r"spot_\d{3}")
_CJK_MAP_EXTENDED = False

CJK_UNICODE_RANGES = [
    (0x3400, 0x4DBF),  # Extension A
    (0x4E00, 0x9FFF),  # Unified Ideographs
    (0xF900, 0xFAFF),  # Compatibility Ideographs
    (0x20000, 0x2A6DF),  # Extension B
    (0x2A700, 0x2B73F),  # Extension C
    (0x2B740, 0x2B81F),  # Extension D
    (0x2B820, 0x2CEAF),  # Extension E
    (0x2CEB0, 0x2EBEF),  # Extension F
]


def _is_cjk(char: str) -> bool:
    code = ord(char)
    for start, end in CJK_UNICODE_RANGES:
        if start <= code <= end:
            return True
    return False


def _extract_cjk(text: str) -> str:
    return "".join(ch for ch in text if _is_cjk(ch))


def _generate_cjk_variants(name: str) -> Set[str]:
    variants: Set[str] = set()
    simplified = name.translate(TRAD_TO_SIMPLIFIED_MAP)
    if simplified != name:
        variants.add(simplified)
    traditional = name.translate(SIMPLIFIED_TO_TRAD_MAP)
    if traditional != name:
        variants.add(traditional)
    return variants

def _get_spot_display_name_for_text(spot_id: str, lang: Optional[str]) -> str:
    catalog = _load_spot_catalog()
    info = catalog["info"].get(spot_id) or {}
    official = info.get("official_name") or {}
    language_candidates: List[str] = []
    if lang:
        normalized = lang.split("-")[0].lower()
        language_candidates.append(normalized)
        if normalized != lang:
            language_candidates.append(lang.lower())
    language_candidates.extend(["zh", "ja", "en"])

    for code in language_candidates:
        value = official.get(code)
        if isinstance(value, str) and value.strip():
            return value.strip()

    aliases = info.get("aliases") or {}
    if isinstance(aliases, dict):
        for code in language_candidates:
            for alias in aliases.get(code) or []:
                if isinstance(alias, str) and alias.strip():
                    return alias.strip()
        for alias_list in aliases.values():
            if isinstance(alias_list, list):
                for alias in alias_list:
                    if isinstance(alias, str) and alias.strip():
                        return alias.strip()

    display_name = info.get("name") or info.get("spot_name")
    if isinstance(display_name, str) and display_name.strip():
        return display_name.strip()
    return spot_id

def _replace_spot_ids_with_names(text: str, lang: Optional[str]) -> str:
    if not text:
        return text

    def _repl(match: re.Match) -> str:
        spot_id = match.group(0)
        return _get_spot_display_name_for_text(spot_id, lang)

    return SPOT_ID_PATTERN.sub(_repl, text)


def _extend_cjk_maps(data: List[Dict[str, Any]]) -> None:
    global _CJK_MAP_EXTENDED
    if _CJK_MAP_EXTENDED:
        return

    trad_updates: Dict[int, str] = {}
    simp_updates: Dict[int, str] = {}

    for item in data:
        names = item.get("official_name") or {}
        ja_name = _extract_cjk(names.get("ja", ""))
        zh_name = _extract_cjk(names.get("zh", ""))
        if not ja_name or not zh_name:
            continue
        if len(ja_name) != len(zh_name):
            continue
        for ja_char, zh_char in zip(ja_name, zh_name):
            if ja_char and zh_char and ja_char != zh_char:
                trad_updates[ord(ja_char)] = zh_char
                simp_updates[ord(zh_char)] = ja_char

    if trad_updates:
        TRAD_TO_SIMPLIFIED_MAP.update(trad_updates)
    if simp_updates:
        SIMPLIFIED_TO_TRAD_MAP.update(simp_updates)
    _CJK_MAP_EXTENDED = True

def _normalize_text(value: str) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.lower()
    for ch in (" ", "　", "\n", "\t"):
        normalized = normalized.replace(ch, "")
    return normalized


@lru_cache(maxsize=1)
def _load_spot_catalog() -> Dict[str, Any]:
    base_dir = Path(__file__).resolve().parent
    info_path = base_dir / "processed" / "spot_info.json"
    with info_path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)

    info_map: Dict[str, Dict[str, Any]] = {}
    name_index: Dict[str, Set[str]] = {}
    names_by_id: Dict[str, Set[str]] = {}

    _extend_cjk_maps(data)

    for item in data:
        spot_id = item.get("spot_id")
        if not spot_id:
            continue
        info_map[spot_id] = item
        names: Set[str] = set()
        for name in (item.get("official_name") or {}).values():
            if name:
                names.add(name)
                names.update(_generate_cjk_variants(name))
        for alias_list in (item.get("aliases") or {}).values():
            if isinstance(alias_list, list):
                for alias in alias_list:
                    if alias:
                        names.add(alias)
                        names.update(_generate_cjk_variants(alias))

        for extra_alias in ADDITIONAL_ALIASES.get(spot_id, []):
            if extra_alias:
                names.add(extra_alias)
                names.update(_generate_cjk_variants(extra_alias))

        names_by_id[spot_id] = names
        for name in names:
            key = _normalize_text(name)
            if key:
                name_index.setdefault(key, set()).add(spot_id)

    return {"info": info_map, "name_index": name_index, "names_by_id": names_by_id}


def _collect_entity_strings(payload: Any) -> Tuple[List[str], List[str]]:
    spot_ids: List[str] = []
    spot_names: List[str] = []

    def _collect(value: Any):
        if isinstance(value, str):
            if value.startswith("spot_"):
                spot_ids.append(value)
            else:
                spot_names.append(value)
        elif isinstance(value, list):
            for item in value:
                _collect(item)
        elif isinstance(value, dict):
            for item in value.values():
                _collect(item)

    _collect(payload)
    return spot_ids, spot_names


def _get_last_recommendations(state: AgentState) -> List[Dict[str, Any]]:
    recs = state.get("recommendations") or []
    if recs:
        return recs
    profile = state.get("user_profile") or {}
    return profile.get("_last_recommendations", [])


def _order_candidates(candidates: List[str], preferred_order: List[str]) -> List[str]:
    seen: Set[str] = set()
    ordered: List[str] = []
    preferred_index = {spot_id: idx for idx, spot_id in enumerate(preferred_order)}
    candidates_sorted = sorted(
        candidates,
        key=lambda sid: preferred_index.get(sid, len(preferred_order))
    )
    for spot_id in candidates_sorted:
        if spot_id and spot_id not in seen:
            ordered.append(spot_id)
            seen.add(spot_id)
    return ordered


def _lookup_spot_ids_by_name(name: str, preferred_order: List[str], allowed_ids: Optional[Set[str]] = None) -> List[str]:
    catalog = _load_spot_catalog()
    name_index: Dict[str, Set[str]] = catalog["name_index"]
    query_variants = {name}
    query_variants.update(_generate_cjk_variants(name))

    normalized_variants = {_normalize_text(variant) for variant in query_variants if _normalize_text(variant)}
    if not normalized_variants:
        return []

    matches: Set[str] = set()
    for normalized in normalized_variants:
        if normalized in name_index:
            matches.update(name_index[normalized])

    if not matches:
        preferred_set = set(preferred_order)
        for normalized in normalized_variants:
            for key, spot_ids in name_index.items():
                if normalized and normalized in key:
                    for sid in spot_ids:
                        if sid in preferred_set:
                            matches.add(sid)

    if not matches:
        threshold = 0.65
        for normalized in normalized_variants:
            if not normalized:
                continue
            for key, spot_ids in name_index.items():
                ratio = difflib.SequenceMatcher(None, normalized, key).ratio()
                if ratio >= threshold:
                    matches.update(spot_ids)

    if allowed_ids is not None:
        matches = {sid for sid in matches if sid in allowed_ids}

    return _order_candidates(list(matches), preferred_order)


def _resolve_spot_reference(reference: Optional[str], itinerary: List[str]) -> Optional[str]:
    """
    reorder_plan などで指定されたスポット名／IDから、現在の旅程に存在するspot_idを解決する。
    """
    if not reference:
        return None

    itinerary_ids: Set[str] = set(itinerary)
    if reference in itinerary_ids:
        return reference

    matched = _lookup_spot_ids_by_name(reference, itinerary, itinerary_ids)
    if matched:
        return matched[0]
    return None


def _extract_recommended_matches_from_input(state: AgentState) -> List[str]:
    catalog = _load_spot_catalog()
    names_by_id: Dict[str, Set[str]] = catalog["names_by_id"]
    recs = _get_last_recommendations(state)
    if not recs:
        return []

    normalized_input = _normalize_text(state.get("user_input", ""))
    result: List[str] = []
    for rec in recs:
        spot_id = rec.get("spot_id")
        if not spot_id:
            continue
        for name in names_by_id.get(spot_id, []):
            if _normalize_text(name) and _normalize_text(name) in normalized_input:
                result.append(spot_id)
                break
    return result


def _extract_itinerary_matches_from_input(state: AgentState) -> List[str]:
    catalog = _load_spot_catalog()
    names_by_id: Dict[str, Set[str]] = catalog["names_by_id"]
    itinerary = state.get("itinerary", []) or []
    if not itinerary:
        return []

    normalized_input = _normalize_text(state.get("user_input", ""))
    result: List[str] = []
    for spot_id in itinerary:
        for name in names_by_id.get(spot_id, []):
            if _normalize_text(name) and _normalize_text(name) in normalized_input:
                result.append(spot_id)
                break
    return result


ORDINAL_PATTERNS = {
    0: ["一番", "１番", "1番", "一つ", "１つ", "1つ", "ひとつ", "最初", "はじめ", "first", "1st"],
    1: ["二番", "２番", "2番", "二つ", "２つ", "2つ", "ふたつ", "second", "2nd"],
    2: ["三番", "３番", "3番", "三つ", "３つ", "3つ", "みっつ", "third", "3rd"],
    3: ["四番", "４番", "4番", "四つ", "４つ", "4つ", "よっつ", "fourth", "4th"],
    4: ["五番", "５番", "5番", "五つ", "５つ", "5つ", "いつつ", "fifth", "5th"],
}
ORDINAL_LAST_TOKENS = ["最後", "ラスト", "last"]

WEATHER_LABELS = {
    "ja": {0: "晴れ", 1: "曇り", 2: "雨", "unknown": "不明"},
    "en": {0: "Sunny", 1: "Cloudy", 2: "Rainy", "unknown": "Unknown"},
    "zh": {0: "晴朗", 1: "多云", 2: "下雨", "unknown": "未知"},
}

CONGESTION_LABELS = {
    "ja": {0: "空いています", 1: "やや混雑しています", 2: "混雑しています", "unknown": "不明"},
    "en": {0: "Not crowded", 1: "Somewhat crowded", 2: "Crowded", "unknown": "Unknown"},
    "zh": {0: "较为空闲", 1: "稍有拥挤", 2: "非常拥挤", "unknown": "未知"},
}

PROFILE_FIELDS = ["age", "travel_style", "travel_frequency", "interests"]


_PROFILE_RECOMMENDER_KEYS = {
    "travel_style": "Travel_Style",
    "travel_frequency": "Travel_Frequency",
    "interests": "Preferred_Destinations",
    # 既に大文字を使って保存されているケースにも対応
    "preferred_destinations": "Preferred_Destinations",
}

_NORMALIZE_PUNCT_PATTERN = re.compile(r"[^\w\s/;]+")


def _normalize_profile_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        text = str(value)
    else:
        text = str(value)
    text = unicodedata.normalize("NFKC", text)
    text = text.lower()
    text = _NORMALIZE_PUNCT_PATTERN.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _build_synonym_map(categories: List[str], synonym_table: Dict[str, Set[str]]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for canonical in categories:
        normalized = _normalize_profile_text(canonical)
        mapping[normalized] = canonical
        for synonym in synonym_table.get(canonical, set()):
            normalized_syn = _normalize_profile_text(synonym)
            if normalized_syn:
                mapping[normalized_syn] = canonical
    return mapping


_TRAVEL_STYLE_SYNONYM_MAP = _build_synonym_map(TRAVEL_STYLE_CATEGORIES, TRAVEL_STYLE_SYNONYMS)
_TRAVEL_STYLE_CANONICAL_LOOKUP = { _normalize_profile_text(item): item for item in TRAVEL_STYLE_CATEGORIES }
_TRAVEL_STYLE_NORMALIZED = list(_TRAVEL_STYLE_CANONICAL_LOOKUP.keys())

_TRAVEL_FREQUENCY_SYNONYM_MAP = _build_synonym_map(TRAVEL_FREQUENCY_CATEGORIES, TRAVEL_FREQUENCY_SYNONYMS)
_TRAVEL_FREQUENCY_CANONICAL_LOOKUP = { _normalize_profile_text(item): item for item in TRAVEL_FREQUENCY_CATEGORIES }
_TRAVEL_FREQUENCY_NORMALIZED = list(_TRAVEL_FREQUENCY_CANONICAL_LOOKUP.keys())

_PREFERRED_DEST_NORMALIZED = { _normalize_profile_text(item): item for item in PREFERRED_DESTINATION_CATEGORIES }
_PREFERRED_DEST_NORMALIZED_LIST = list(_PREFERRED_DEST_NORMALIZED.keys())
_PREFERRED_COMPONENT_SYNONYMS_NORMALIZED = {
    component: { _normalize_profile_text(token) for token in tokens if _normalize_profile_text(token) }
    for component, tokens in PREFERRED_DESTINATION_COMPONENT_SYNONYMS.items()
}
_PREFERRED_COMPONENT_COMBOS: Dict[frozenset[str], str] = {}
for canonical in PREFERRED_DESTINATION_CATEGORIES:
    parts = tuple(part.strip() for part in canonical.split(";") if part.strip())
    if not parts:
        continue
    key = frozenset(parts)
    _PREFERRED_COMPONENT_COMBOS.setdefault(key, canonical)

_PREFERRED_COMPONENT_ORDER = ["Beaches", "Cities", "Historical Sites", "Mountains", "Nature/forests"]


def _match_with_similarity(normalized_value: str, normalized_candidates: List[str], canonical_lookup: Dict[str, str], cutoff: float = 0.75) -> Optional[str]:
    if not normalized_value:
        return None
    matches = difflib.get_close_matches(normalized_value, normalized_candidates, n=1, cutoff=cutoff)
    if matches:
        return canonical_lookup.get(matches[0])
    return None


def _iterate_profile_values(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [item for item in value if item not in (None, "")]
    return [value]


def _resolve_travel_style_value(value: Any) -> Optional[str]:
    for item in _iterate_profile_values(value):
        normalized = _normalize_profile_text(item)
        if not normalized:
            continue
        direct = _TRAVEL_STYLE_SYNONYM_MAP.get(normalized)
        if direct:
            return direct
    for item in _iterate_profile_values(value):
        normalized = _normalize_profile_text(item)
        if not normalized:
            continue
        similar = _match_with_similarity(normalized, _TRAVEL_STYLE_NORMALIZED, _TRAVEL_STYLE_CANONICAL_LOOKUP, cutoff=0.6)
        if similar:
            return similar
    return None


def _resolve_travel_frequency_value(value: Any) -> Optional[str]:
    for item in _iterate_profile_values(value):
        normalized = _normalize_profile_text(item)
        if not normalized:
            continue
        direct = _TRAVEL_FREQUENCY_SYNONYM_MAP.get(normalized)
        if direct:
            return direct
    for item in _iterate_profile_values(value):
        normalized = _normalize_profile_text(item)
        if not normalized:
            continue
        similar = _match_with_similarity(normalized, _TRAVEL_FREQUENCY_NORMALIZED, _TRAVEL_FREQUENCY_CANONICAL_LOOKUP, cutoff=0.6)
        if similar:
            return similar
    return None


def _extract_destination_components(value: Any) -> Set[str]:
    normalized = _normalize_profile_text(value)
    if not normalized:
        return set()
    direct = _PREFERRED_DEST_NORMALIZED.get(normalized)
    if direct:
        return {part.strip() for part in direct.split(";") if part.strip()}
    components: Set[str] = set()
    for component, tokens in _PREFERRED_COMPONENT_SYNONYMS_NORMALIZED.items():
        if any(token and token in normalized for token in tokens):
            components.add(component)
    return components


def _components_to_canonical(components: Set[str]) -> Optional[str]:
    if not components:
        return None
    key = frozenset(components)
    direct = _PREFERRED_COMPONENT_COMBOS.get(key)
    if direct:
        return direct
    ordered = [component for component in _PREFERRED_COMPONENT_ORDER if component in components]
    if not ordered:
        return None
    candidate = ";".join(ordered)
    normalized = _normalize_profile_text(candidate)
    direct_candidate = _PREFERRED_DEST_NORMALIZED.get(normalized)
    if direct_candidate:
        return direct_candidate
    similar = _match_with_similarity(normalized, _PREFERRED_DEST_NORMALIZED_LIST, _PREFERRED_DEST_NORMALIZED, cutoff=0.6)
    return similar


def _resolve_preferred_destinations_value(value: Any) -> Optional[str]:
    items = _iterate_profile_values(value)
    if not items:
        return None

    accumulated_components: Set[str] = set()
    fallback_candidates: List[str] = []

    for item in items:
        normalized = _normalize_profile_text(item)
        if not normalized:
            continue

        direct = _PREFERRED_DEST_NORMALIZED.get(normalized)
        if direct:
            parts = {part.strip() for part in direct.split(";") if part.strip()}
            accumulated_components.update(parts)
            fallback_candidates.append(direct)
            continue

        components = _extract_destination_components(item)
        if components:
            accumulated_components.update(components)
            continue

        similar = _match_with_similarity(normalized, _PREFERRED_DEST_NORMALIZED_LIST, _PREFERRED_DEST_NORMALIZED, cutoff=0.6)
        if similar:
            parts = {part.strip() for part in similar.split(";") if part.strip()}
            accumulated_components.update(parts)
            fallback_candidates.append(similar)

    canonical = _components_to_canonical(accumulated_components)
    if canonical:
        return canonical

    for candidate in fallback_candidates:
        if candidate:
            return candidate

    for item in items:
        normalized = _normalize_profile_text(item)
        similar = _match_with_similarity(normalized, _PREFERRED_DEST_NORMALIZED_LIST, _PREFERRED_DEST_NORMALIZED, cutoff=0.5)
        if similar:
            return similar
    return None


def _get_profile_value(profile: dict, keys: Tuple[str, ...]) -> Any:
    for key in keys:
        value = profile.get(key)
        if value not in (None, "", []):
            return value
    return None


def _profile_for_recommender(profile: dict) -> Dict[str, Any]:
    """Recommender モデルが想定するキーへユーザープロファイルを変換する。"""
    if not profile:
        return {}

    mapped: Dict[str, Any] = {}

    travel_style_raw = _get_profile_value(profile, ("Travel_Style", "travel_style"))
    travel_style = _resolve_travel_style_value(travel_style_raw)
    if travel_style:
        mapped["Travel_Style"] = travel_style
    elif isinstance(travel_style_raw, str) and travel_style_raw.strip():
        mapped["Travel_Style"] = travel_style_raw.strip()

    travel_frequency_raw = _get_profile_value(profile, ("Travel_Frequency", "travel_frequency"))
    travel_frequency = _resolve_travel_frequency_value(travel_frequency_raw)
    if travel_frequency:
        mapped["Travel_Frequency"] = travel_frequency
    elif isinstance(travel_frequency_raw, str) and travel_frequency_raw.strip():
        mapped["Travel_Frequency"] = travel_frequency_raw.strip()

    preferred_raw = _get_profile_value(profile, ("Preferred_Destinations", "preferred_destinations", "interests"))
    preferred = _resolve_preferred_destinations_value(preferred_raw)
    if preferred:
        mapped["Preferred_Destinations"] = preferred
    elif isinstance(preferred_raw, str) and preferred_raw.strip():
        mapped["Preferred_Destinations"] = preferred_raw.strip()

    return mapped


PROFILE_LOCALIZATION = {
    "ja": {
        "user_prefix": "ユーザー発話: {input}",
        "focus_prefix": "期待する項目: {fields}",
        "question_fallback": "教えていただきたい情報は特にありません。",
        "question_intro": "教えていただける範囲で、次のことを教えてもらえますか？",
        "encourage": "どれか一つでも構いませんし、答えやすい範囲で大丈夫です！",
        "hints": {
            "age": "年齢層（例: 20代 / 30s / 50歳くらい）を教えていただけますか？",
            "travel_style": "旅のスタイル（例: ひとり旅 / 家族でゆったり / アクティブに歩く）があれば教えてください。",
            "travel_frequency": "旅行の頻度（例: 年に1-2回 / 毎月 / 初めて）を教えていただけますか？",
            "interests": "興味のあるジャンル（例: 温泉 / グルメ / 景色など）があれば教えてください。",
        },
    },
    "en": {
        "user_prefix": "User utterance: {input}",
        "focus_prefix": "Expected fields: {fields}",
        "question_fallback": "There is nothing we need to ask right now.",
        "question_intro": "If you do not mind, could you share any of the following?",
        "encourage": "Even one item is perfectly fine—please answer only what you’re comfortable sharing!",
        "hints": {
            "age": "Age range (e.g., 20s / 30s / around 50).",
            "travel_style": "Travel style (e.g., solo trip / relaxed family trip / active sightseeing).",
            "travel_frequency": "Trip frequency (e.g., 1-2 times a year / monthly / first visit).",
            "interests": "Interests (e.g., hot springs / gourmet food / scenery).",
        },
    },
    "zh": {
        "user_prefix": "用户发言: {input}",
        "focus_prefix": "期待了解的项目: {fields}",
        "question_fallback": "目前没有需要再询问的信息。",
        "question_intro": "方便的话，请告诉我以下信息：",
        "encourage": "回答其中任意一项即可，按照自己舒服的程度分享就好！",
        "hints": {
            "age": "年龄范围（例如：20多岁 / 30岁左右 / 大约50岁）。",
            "travel_style": "旅行风格（例如：独自旅行 / 家庭轻松游 / 喜欢活动）。",
            "travel_frequency": "旅行频率（例如：每年1-2次 / 每月 / 第一次）。",
            "interests": "感兴趣的主题（例如：温泉 / 美食 / 风景）。",
        },
    },
}

INTENT_PROMPT_LOCALIZATION = {
    "ja": {
        "history_prefix": "会話履歴:\n{history}\n\nユーザーの最新の発話: {input}\n",
    },
    "en": {
        "history_prefix": "Conversation history:\n{history}\n\nLatest user utterance: {input}\n",
    },
    "zh": {
        "history_prefix": "会话历史：\n{history}\n\n最新的用户发言：{input}\n",
    },
}

def _select_language(lang: Optional[str]) -> str:
    if not lang:
        return "ja"
    normalized = lang.split("-")[0].lower()
    if normalized not in WEATHER_LABELS:
        return "ja"
    return normalized

def _weather_label(value: Optional[int], lang: Optional[str] = None) -> str:
    labels = WEATHER_LABELS[_select_language(lang)]
    if value is None:
        return labels["unknown"]
    return labels.get(value, labels["unknown"])

def _congestion_label(value: Optional[int], lang: Optional[str] = None) -> str:
    labels = CONGESTION_LABELS[_select_language(lang)]
    if value is None:
        return labels["unknown"]
    return labels.get(value, labels["unknown"])


def _profile_has_information(profile: dict) -> bool:
    if not profile:
        return False
    return any(bool(profile.get(field)) for field in PROFILE_FIELDS)


def _missing_profile_fields(profile: dict) -> List[str]:
    return [field for field in PROFILE_FIELDS if not profile.get(field)]


def _clean_profile_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    cleaned: Dict[str, Any] = {}
    for key in PROFILE_FIELDS + ["positive_keywords", "negative_keywords"]:
        value = payload.get(key)
        if value in (None, ""):
            continue
        if key == "interests" or key.endswith("keywords"):
            if isinstance(value, list):
                items = [str(v).strip() for v in value if str(v).strip()]
                if items:
                    cleaned[key] = items
            elif isinstance(value, str) and value.strip():
                cleaned[key] = [value.strip()]
        else:
            cleaned[key] = str(value).strip()
    return cleaned


def _strip_llm_artifacts(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL)
    text = text.strip()
    if text.startswith("<json>") and text.endswith("</json>"):
        text = text[len("<json>"):-len("</json>")].strip()
    fence_match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()
    return text


def _merge_profile(existing: dict, updates: Dict[str, Any]) -> bool:
    if not updates:
        return False
    changed = False
    for key, value in updates.items():
        if key in ("interests", "positive_keywords", "negative_keywords"):
            existing_list = existing.get(key) or []
            if not isinstance(existing_list, list):
                existing_list = [existing_list]
            new_values = [v for v in value if v not in existing_list]
            if new_values:
                existing[key] = existing_list + new_values
                changed = True
        else:
            if value and existing.get(key) != value:
                existing[key] = value
                changed = True
    return changed


def _extract_profile_from_text(user_input: str, awaiting_fields: List[str], language: Optional[str]) -> Dict[str, Any]:
    if not user_input or not user_input.strip():
        return {}

    locale = PROFILE_LOCALIZATION[_select_language(language)]

    system_prompt = get_profile_extractor_prompt(language)
    target_fields = awaiting_fields or PROFILE_FIELDS
    focus_clause = locale["focus_prefix"].format(fields=", ".join(target_fields)) if target_fields else ""
    lines = [locale["user_prefix"].format(input=user_input)]
    if focus_clause:
        lines.append(focus_clause)
    user_prompt = "\n".join(lines)

    try:
        raw_response = generate_text(system_prompt, user_prompt, temperature=0.0, json_mode=True)
    except Exception as exc:
        print(f"[WARN] profile extractor failed: {exc}")
        return {}

    cleaned = _strip_llm_artifacts(raw_response)
    if not cleaned:
        return {}
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        print(f"[WARN] failed to decode profile JSON: {cleaned} ({exc})")
        return {}

    updates = _clean_profile_payload(payload or {})
    return _filter_profile_updates(user_input, awaiting_fields, updates)


def _filter_profile_updates(user_input: str, awaiting_fields: List[str], updates: Dict[str, Any]) -> Dict[str, Any]:
    if not updates:
        return {}

    normalized_input = _normalize_text(user_input)
    allowed_keys = set(awaiting_fields) if awaiting_fields else set(PROFILE_FIELDS)

    def _value_in_input(candidate: str) -> bool:
        token = _normalize_text(candidate)
        return bool(token) and token in normalized_input

    filtered: Dict[str, Any] = {}
    for key, value in updates.items():
        if allowed_keys and key not in allowed_keys:
            continue
        if isinstance(value, list):
            kept = [item for item in value if _value_in_input(item)]
            if kept:
                filtered[key] = kept
        else:
            if isinstance(value, str) and _value_in_input(value):
                filtered[key] = value
    return filtered


def _generate_profile_question(fields: List[str], language: Optional[str]) -> str:
    locale = PROFILE_LOCALIZATION[_select_language(language)]
    if not fields:
        return locale["question_fallback"]
    system_prompt = get_profile_question_prompt(language)
    user_prompt = json.dumps({"missing_fields": fields}, ensure_ascii=False)
    try:
        text = generate_text(system_prompt, user_prompt).strip()
        if text:
            return text
    except Exception as exc:
        print(f"[WARN] profile question generation failed: {exc}")

    hints = locale["hints"]
    lines = [locale["question_intro"]]
    for field in fields[:3]:
        example = hints.get(field)
        if example:
            lines.append(f"- {example}")
    lines.append(locale["encourage"])
    return "\n".join(lines)


def _resolve_spots_from_ordinals(state: AgentState, rec_ids: List[str]) -> List[str]:
    if not rec_ids:
        return []
    normalized_input = _normalize_text(state.get("user_input", ""))
    if not normalized_input:
        return []

    results: List[str] = []
    for index, tokens in ORDINAL_PATTERNS.items():
        if index >= len(rec_ids):
            continue
        if any(_normalize_text(token) in normalized_input for token in tokens):
            results.append(rec_ids[index])
    if not results and any(_normalize_text(token) in normalized_input for token in ORDINAL_LAST_TOKENS):
        results.append(rec_ids[-1])
    return results


def _resolve_spot_ids_to_add(state: AgentState) -> List[str]:
    parsed = state.get("parsed_intent", {})
    entities = parsed.get("entities", {}) or {}
    recs = _get_last_recommendations(state)
    preferred_order = [rec.get("spot_id") for rec in recs if rec.get("spot_id")]

    resolved_ids: List[str] = []
    seen: Set[str] = set()

    def _append(spot_id: str):
        if spot_id and spot_id not in seen:
            resolved_ids.append(spot_id)
            seen.add(spot_id)

    entity_spot_ids, entity_spot_names = _collect_entity_strings(entities)
    for spot_id in entity_spot_ids:
        _append(spot_id)
    for name in entity_spot_names:
        result_from_preferred = _lookup_spot_ids_by_name(name, preferred_order, set(preferred_order))
        for spot_id in result_from_preferred:
            _append(spot_id)
        if not result_from_preferred:
            for spot_id in _lookup_spot_ids_by_name(name, preferred_order):
                _append(spot_id)

    # ユーザー入力から推奨スポット名を検出
    for spot_id in _extract_recommended_matches_from_input(state):
        _append(spot_id)

    # 推薦リストの序番号参照（例:「一番目」など）
    for spot_id in _resolve_spots_from_ordinals(state, preferred_order):
        _append(spot_id)

    return resolved_ids


def _resolve_spot_ids_to_delete(state: AgentState) -> List[str]:
    parsed = state.get("parsed_intent", {})
    entities = parsed.get("entities", {}) or {}
    itinerary = state.get("itinerary", []) or []
    preferred_order = itinerary

    resolved_ids: List[str] = []
    seen: Set[str] = set()

    def _append(spot_id: str):
        if spot_id and spot_id not in seen:
            resolved_ids.append(spot_id)
            seen.add(spot_id)

    entity_spot_ids, entity_spot_names = _collect_entity_strings(entities)
    for spot_id in entity_spot_ids:
        if spot_id in itinerary:
            _append(spot_id)
    for name in entity_spot_names:
        for spot_id in _lookup_spot_ids_by_name(name, preferred_order, set(itinerary)):
            if spot_id in itinerary:
                _append(spot_id)

    for spot_id in _extract_itinerary_matches_from_input(state):
        _append(spot_id)

    for spot_id in _resolve_spots_from_ordinals(state, preferred_order):
        if spot_id in itinerary:
            _append(spot_id)

    return resolved_ids


# --- 2. Nodes (ノード) の実装 --- #


def collect_user_profile(state: AgentState):
    print("--- Node: collect_user_profile ---")
    profile = dict(state.get("user_profile") or {})
    awaiting = list(state.get("awaiting_profile_fields") or [])
    language = state.get("language")

    updates = _extract_profile_from_text(state.get("user_input", ""), awaiting, language)
    changed = _merge_profile(profile, updates)

    if changed:
        print(f"[Profile] Updated user profile with: {updates}")
        db = next(db_client.get_db_session())
        user = db_client.get_or_create_user(db, state.get("user_name"), state.get("language") or "ja")
        db_client.update_user(db, user.id, profile, state.get("itinerary", []))

    if awaiting:
        awaiting = [field for field in awaiting if not profile.get(field)]

    return {
        "user_profile": profile,
        "awaiting_profile_fields": awaiting,
    }


def ask_for_profile(state: AgentState):
    print("--- Node: ask_for_profile ---")
    profile = state.get("user_profile") or {}
    awaiting = list(state.get("awaiting_profile_fields") or [])

    missing = _missing_profile_fields(profile)
    remaining = [field for field in missing if field not in awaiting]
    if not remaining:
        remaining = missing

    fields_to_ask = remaining[:3]
    if not fields_to_ask:
        print("[Profile] No missing fields detected; skipping ask_for_profile.")
        return {}

    question_text = _generate_profile_question(fields_to_ask, state.get("language"))

    return {
        "response_text": question_text,
        "awaiting_profile_fields": fields_to_ask,
        "recommendations": [],
    }


def start_session(state: AgentState):
    print("--- Node: start_session ---")
    db = next(db_client.get_db_session())
    preferred_language = state.get('language')
    if preferred_language:
        user = db_client.get_or_create_user(db, state['user_name'], preferred_language)
    else:
        user = db_client.get_or_create_user(db, state['user_name'])

    language_code = getattr(user.language, "value", "ja")

    profile = dict(user.user_profile or {})
    last_recs = profile.get("_last_recommendations", [])

    return {
        "user_profile": profile,
        "itinerary": user.current_itinerary or [],
        "language": language_code,
        "recommendations": last_recs,
        "awaiting_profile_fields": state.get("awaiting_profile_fields", []),
    }

def build_context(state: AgentState):
    print("--- Node: build_context ---")
    db = next(db_client.get_db_session())
    user = db_client.get_or_create_user(db, state['user_name'])

    # 1. 短期記憶の取得 (直近3ターン = 6メッセージ)
    short_term_sql = db_client.get_conversation_history(db, user.id, limit=6)
    short_term_memory = [{"role": msg.role, "content": msg.content} for msg in reversed(short_term_sql)]
    print(f"Found {len(short_term_memory)} messages in short-term memory.")

    # 2. 長期記憶の取得 (関連性の高い会話2件)
    long_term_docs = vector_store_client.search_similar_conversations(
        query_text=state['user_input'],
        n_results=2
    )
    long_term_memory = [{"role": "system", "content": f"過去の関連する会話の要約: {doc}"} for doc in long_term_docs]
    print(f"Found {len(long_term_memory)} messages in long-term memory.")

    final_history = long_term_memory + short_term_memory
    return {"chat_history": final_history}

JSON_BLOCK_PATTERNS = [
    re.compile(r"<json>\s*(\{.*?\})\s*</json>", re.DOTALL),
    re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL),
]


def _remove_think_blocks(text: str) -> str:
    if not text:
        return text
    return re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL)


def _extract_json_from_text(model_output: str) -> Optional[str]:
    if not model_output:
        return None
    cleaned = _remove_think_blocks(model_output).strip()
    for pattern in JSON_BLOCK_PATTERNS:
        match = pattern.search(cleaned)
        if match:
            return match.group(1)

    start = cleaned.find("{")
    if start == -1:
        return None

    depth = 0
    for idx in range(start, len(cleaned)):
        char = cleaned[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return cleaned[start: idx + 1]
    return None


def _parse_intent_payload(model_output: str) -> Optional[Dict[str, Any]]:
    json_payload = _extract_json_from_text(model_output)
    if not json_payload:
        return None

    try:
        parsed = json.loads(json_payload)
    except json.JSONDecodeError:
        print(f"[ERROR] Failed to decode intent JSON payload: {json_payload}")
        return None

    if not isinstance(parsed, dict):
        return None

    if "intent" not in parsed and "intention" in parsed:
        parsed["intent"] = parsed.pop("intention")

    entities = parsed.get("entities")
    if entities is None:
        parsed["entities"] = {}
    elif not isinstance(entities, dict):
        parsed["entities"] = {"values": entities}

    return parsed


def parse_intent(state: AgentState):
    print("--- Node: parse_intent ---")
    language = state.get("language")
    system_prompt = get_intent_parser_prompt(language)
    locale = INTENT_PROMPT_LOCALIZATION[_select_language(language)]

    chat_history = state.get("chat_history") or []
    history_for_intent = chat_history[-2:]
    base_prompt = locale["history_prefix"].format(history=history_for_intent, input=state['user_input'])

    max_attempts = 4  # 初回 + 最大3回リトライ
    parsed_intent = None
    last_output = ""

    for attempt in range(max_attempts):
        if attempt == 0:
            prompt_to_send = base_prompt
        else:
            retry_instruction = get_intent_parser_retry_prompt(last_output, language)
            prompt_to_send = f"""{retry_instruction}
{base_prompt}"""

        try:
            model_output = generate_text(
                system_prompt,
                prompt_to_send,
                temperature=0.0,
                json_mode=True,
            )
        except Exception as exc:
            print(f"[ERROR] Intent parser LLM call failed (attempt {attempt + 1}): {exc}")
            continue

        last_output = model_output
        parsed_intent = _parse_intent_payload(model_output)
        if parsed_intent:
            break

    if not parsed_intent:
        print(f"[ERROR] Intent parser failed after {max_attempts} attempts.")
        print(f"[ERROR] Last model output: {last_output}")
        parsed_intent = {"intent": "chitchat", "entities": {}}

    print(f"[Intent Parser] Parsed intent: {parsed_intent}")
    return {"parsed_intent": parsed_intent}

def get_recommendations(state: AgentState):
    print("--- Node: get_recommendations ---")
    profile_for_model = _profile_for_recommender(state.get('user_profile') or {})
    recs = rec_engine.get_recommendations(profile_for_model, state['itinerary'])
    spot_ids = [rec.get("spot_id") for rec in recs if rec.get("spot_id")]
    realtime_map = {}
    if spot_ids:
        db = next(db_client.get_db_session())
        realtime_map = db_client.get_spot_realtime_map(db, spot_ids)
    language = state.get("language")
    for rec in recs:
        spot_id = rec.get("spot_id")
        realtime = realtime_map.get(spot_id)
        if realtime:
            rec["realtime"] = {
                "weather": realtime.weather,
                "weather_label": _weather_label(realtime.weather, language),
                "congestion": realtime.congestion,
                "congestion_label": _congestion_label(realtime.congestion, language),
                "updated_at": realtime.updated_at.isoformat() if realtime.updated_at else None,
            }
        else:
            rec["realtime"] = None
    print(f"[Recommender] Output: {recs}")
    return {"recommendations": recs}

LLM_ERROR_MESSAGES = {
    "ja": "申し訳ありません、現在AIアシスタントでエラーが発生しています。しばらくしてからもう一度お試しください。",
    "en": "We're sorry—the AI assistant is currently experiencing an error. Please try again shortly.",
    "zh": "非常抱歉，AI助手目前出现错误，请稍后再试。",
}


def generate_response(state: AgentState):
    print("--- Node: generate_response ---")
    system_prompt = get_response_generator_prompt(state)
    try:
        response_text = generate_text(system_prompt, state['user_input'], state['chat_history'])
    except Exception as exc:
        print(f"[ERROR] Response generation failed: {exc}")
        return {"response_text": LLM_ERROR_MESSAGES[_select_language(state.get("language"))]}

    cleaned_response = _remove_think_blocks(response_text)
    cleaned_response = _replace_spot_ids_with_names(cleaned_response, state.get("language"))
    return {"response_text": cleaned_response}

def update_itinerary(state: AgentState):
    """ユーザーの意図に基づき周遊計画を更新する"""
    print("--- Node: update_itinerary ---")
    intent_data = state.get('parsed_intent', {})
    intent = intent_data.get('intent')
    entities = intent_data.get('entities', {}) or {}
    if not isinstance(entities, dict):
        entities = {"spot_names": entities}
    
    # 現在の旅程をコピーして編集
    new_itinerary = state.get('itinerary', []).copy()

    if intent == 'add_to_plan':
        spot_ids_to_add = _resolve_spot_ids_to_add(state)
        if not spot_ids_to_add:
            print(f"[WARN] No spot could be resolved from user input: {state.get('user_input')}")
        for spot_id in spot_ids_to_add:
            if spot_id not in new_itinerary:
                new_itinerary.append(spot_id)
                print(f"Added '{spot_id}' to itinerary.")
            else:
                print(f"'{spot_id}' already in itinerary; skipping.")

    elif intent == 'delete_from_plan':
        spot_ids_to_delete = _resolve_spot_ids_to_delete(state)
        if not spot_ids_to_delete:
            print(f"[WARN] No spot could be resolved for deletion from input: {state.get('user_input')}")
        else:
            for spot_id in spot_ids_to_delete:
                if spot_id in new_itinerary:
                    new_itinerary = [spot for spot in new_itinerary if spot != spot_id]
                    print(f"Deleted '{spot_id}' from itinerary.")
                else:
                    print(f"[WARN] '{spot_id}' not present in itinerary; cannot delete.")

    elif intent == 'reorder_plan':
        reorder_info = entities.get('reorder_info', {}) or {}
        target_ref = reorder_info.get('target_spot')
        dest_ref = reorder_info.get('destination_spot')
        position = reorder_info.get('position')

        target_spot = _resolve_spot_reference(target_ref, new_itinerary)
        dest_spot = _resolve_spot_reference(dest_ref, new_itinerary) if dest_ref else None

        if target_spot and target_spot in new_itinerary:
            # 対象を一度削除
            new_itinerary.remove(target_spot)
            
            if position == 'first':
                new_itinerary.insert(0, target_spot)
            elif position == 'last':
                new_itinerary.append(target_spot)
            elif dest_spot and dest_spot in new_itinerary:
                dest_index = new_itinerary.index(dest_spot)
                if position == 'before':
                    new_itinerary.insert(dest_index, target_spot)
                elif position == 'after':
                    new_itinerary.insert(dest_index + 1, target_spot)
            else: # 移動先が不明な場合は末尾に追加
                new_itinerary.append(target_spot)
            print(f"Reordered itinerary: {new_itinerary}")
        else:
            print(f"[WARN] Unable to resolve target spot '{target_ref}' within itinerary; reorder skipped.")
    elif intent == 'restart_plan':
        new_itinerary.clear()
        print("Itinerary reset per restart_plan intent.")

    new_itinerary = list(dict.fromkeys(new_itinerary))
    return {"itinerary": new_itinerary}

def end_session(state: AgentState):
    print("--- Node: end_session ---")
    print(f"[DEBUG] response_text in end_session: '{state.get('response_text')}'")
    db = next(db_client.get_db_session())
    user = db_client.get_or_create_user(db, state['user_name'])
    config = state.get("configurable", {})
    session_id = config.get("thread_id", "default_session")

    profile = dict(state.get("user_profile", {}) or {})
    if state.get("recommendations"):
        profile["_last_recommendations"] = state["recommendations"]
    db_client.update_user(db, user.id, profile, state['itinerary'])

    user_msg = db_client.save_conversation(
        db, user_id=user.id, session_id=session_id, role='user', content=state['user_input']
    )
    assistant_msg = db_client.save_conversation(
        db, user_id=user.id, session_id=session_id, role='assistant', content=state['response_text']
    )

    turn_text = f"ユーザー: {state['user_input']}\nAI: {state['response_text']}"
    vector_store_client.add_conversation(
        conversation_id=str(assistant_msg.message_id),
        text=turn_text
    )

    updated_history = list(state.get("chat_history", []))
    updated_history.append({"role": "user", "content": state["user_input"]})
    updated_history.append({"role": "assistant", "content": state["response_text"]})

    updated_state = {
        "user_name": state["user_name"],
        "language": state.get("language"),
        "user_profile": profile,
        "itinerary": state.get("itinerary", []),
        "chat_history": updated_history,
        "user_input": state["user_input"],
        "parsed_intent": state.get("parsed_intent", {}),
        "recommendations": state.get("recommendations", []),
        "response_text": state.get("response_text", ""),
        "awaiting_profile_fields": state.get("awaiting_profile_fields", []),
    }
    print(f"End session returning keys: {list(updated_state.keys())}")
    return updated_state

# --- 3. Graph (グラフ) の構築 --- #
rec_engine = recommender.Recommender()
workflow = StateGraph(AgentState)

workflow.add_node("start_session", start_session)
workflow.add_node("collect_user_profile", collect_user_profile)
workflow.add_node("build_context", build_context)
workflow.add_node("parse_intent", parse_intent)
workflow.add_node("get_recommendations", get_recommendations)
workflow.add_node("generate_response", generate_response)
workflow.add_node("update_itinerary", update_itinerary)
workflow.add_node("ask_for_profile", ask_for_profile)
workflow.add_node("end_session", end_session)

workflow.set_entry_point("start_session")
workflow.add_edge("start_session", "collect_user_profile")
workflow.add_edge("collect_user_profile", "build_context")
workflow.add_edge("build_context", "parse_intent")

def decide_next_node(state: AgentState):
    intent = state['parsed_intent'].get('intent')
    if intent == 'request_recommendation':
        if not _profile_has_information(state.get("user_profile") or {}):
            return "ask_for_profile"
        return "get_recommendations"
    if intent in ['add_to_plan', 'delete_from_plan', 'reorder_plan', 'restart_plan']:
        return "update_itinerary"
    return "generate_response"

workflow.add_conditional_edges(
    "parse_intent",
    decide_next_node,
    {
        "get_recommendations": "get_recommendations",
        "update_itinerary": "update_itinerary",
        "generate_response": "generate_response",
        "ask_for_profile": "ask_for_profile",
    }
)

workflow.add_edge("get_recommendations", "generate_response")
workflow.add_edge("update_itinerary", "generate_response")
workflow.add_edge("generate_response", "end_session")
workflow.add_edge("ask_for_profile", "end_session")
workflow.add_edge("end_session", END)

memory = MemorySaver()
app = workflow.compile(checkpointer=memory)

# --- 4. 実行部分 (サンプル) --- #
if __name__ == '__main__':
    print("AI Agent is ready. Running sample interaction...")
    config = {"configurable": {"thread_id": "user-123"}}
    
    initial_state = {
        "user_name": "Taro Yamada",
        "language": "ja",
        "user_input": "こんにちは！鳥海山に行くんですが、どこかおすすめありますか？"
    }
    
    for event in app.stream(initial_state, config, stream_mode="values"):
        print("--- Current State ---")
        event.pop('user_input', None)
        if 'chat_history' in event and event['chat_history']:
            print(f"Chat history has {len(event['chat_history'])} messages. (Omitted)")
            event['chat_history'] = "...omitted..."
        print(json.dumps(event, indent=2, ensure_ascii=False))
        print("\n")
