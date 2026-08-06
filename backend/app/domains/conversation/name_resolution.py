"""スポット名 → spot_id の名寄せ本実装(段4)。

`Docs/30_design/agent_react_architecture.md` はメインエージェントが
`spot_id` を一切見ない・書かないことを要求する(§3.3)。旅程計画サブエージェント
(フロー2「構築」)は、メインエージェントが書いたスポット名を次の優先順で
`spot_id` に解決する:

0. 序数参照(「2番目」「2番目のやつ」等)は `last_candidates` の `rank` から
   直接解決する(2026-08-04、レビュー是正・裁定9)
1. 現在の旅程に含まれるスポットの名前
2. 直近候補(`last_candidates`)の名前
3. DB(`spot_catalog`)の名寄せスコア
   ―― かな折りたたみ(カタカナ→ひらがな)正規化のうえで
   **正式名完全一致 > 正式名部分一致 > 別名完全一致 > 別名部分一致**
   の優先順で照合する(2026-08-04、レビュー是正: 実データ「黄桜温泉ゆらり」が
   `spot_030`「鳥海温泉 遊楽里」の短い別名「ゆらり」に部分一致して誤解決して
   いた。正式名の**単語単位**(空白区切り)の部分一致もこのティアに含める
   ことで、「黄桜温泉」のような複合名の一部にも正しく当たる)

優先度の高いティアで一意に決まればそこで確定する。あるティアで複数の
`spot_id` に解決してしまう場合は「曖昧」として扱い、そのティアで検索を止める
(下位ティアで偶然一意に絞れても、それは名寄せとして正しくない)。

解決できない名前・曖昧な名前は要素単位で落とし、`describe_ambiguous` で
候補つきの説明文を組み立てて呼び出し元(`itinerary_subagent.py`)へ返す
(ToolError にはしない。C4: 部分不正は要素単位で落とす)。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from app.domains.conversation.state import CandidateReference, SpotFact
from app.domains.itinerary.types import Itinerary

# ひらがな/カタカナの正規化 — カタカナ(ァ..ヶ)をひらがなへ寄せる。
_KATAKANA_START = 0x30A1
_KATAKANA_END = 0x30F6
_KATAKANA_TO_HIRAGANA_OFFSET = 0x60
_WHITESPACE_AND_DOT_RE = re.compile(r"[\s・･]")
_TOKEN_SPLIT_RE = re.compile(r"[\s・･]+")
# 短すぎる語は部分一致すると無関係な地点まで拾ってしまうため対象外にする。
_MIN_PARTIAL_MATCH_LENGTH = 2
_MAX_AMBIGUOUS_CANDIDATES = 5
# 「2番目」「2番目のやつ」「1つ目」「3個目」等の序数参照(§5 フロー2)。
# 全角数字は normalize 前の NFKC で半角へ揃えてからマッチする。
_ORDINAL_RE = re.compile(r"^\s*(\d+)\s*(?:番目|番|つ目|個目)")


def _tokenize(value: str) -> list[str]:
    """空白・中黒区切りの単語へ分ける(正規化前の生文字列に対して行う)。

    「黄桜温泉 湯楽里」のような複合名の構成要素(単語)単位でも部分一致
    できるようにするための索引作成専用の補助。
    """

    return [part for part in _TOKEN_SPLIT_RE.split(value.strip()) if part]


def normalize_name(value: str) -> str:
    """ひらがな/カタカナ・全角半角・空白/中黒の表記ゆれを 1 つの照合キーへ畳む。"""

    normalized = unicodedata.normalize("NFKC", value)
    stripped = _WHITESPACE_AND_DOT_RE.sub("", normalized)
    folded = "".join(
        chr(ord(ch) - _KATAKANA_TO_HIRAGANA_OFFSET)
        if _KATAKANA_START <= ord(ch) <= _KATAKANA_END
        else ch
        for ch in stripped
    )
    return folded.casefold()


@dataclass(frozen=True, slots=True)
class NameMatch:
    """1 回の名前解決の結果。"""

    status: Literal["resolved", "ambiguous", "unresolved"]
    spot_id: str | None = None
    candidates: tuple[str, ...] = ()  # 曖昧時の候補名(表示用。spot_id は含めない)


def describe_ambiguous(name: str, match: NameMatch) -> str:
    """曖昧だった要素を候補つきの日本語 1 行にする(フロー4で使う)。"""

    shown = match.candidates[:_MAX_AMBIGUOUS_CANDIDATES]
    hidden = len(match.candidates) - len(shown)
    suffix = f" 他{hidden}件" if hidden > 0 else ""
    return f"{name}(候補: {' / '.join(shown)}{suffix})"


@dataclass(frozen=True, slots=True)
class NameResolutionOutcome:
    """複数名をまとめて解決した結果。"""

    resolved: list[str]
    dropped: list[str]  # 完全に解決できなかった名前(そのまま)
    ambiguous: list[str]  # 曖昧だった名前(`describe_ambiguous` で整形済み)


@dataclass(frozen=True, slots=True)
class NameResolutionContext:
    """1 回の Tool 呼び出しの間だけ使う、優先度つきの名前解決索引。"""

    spot_catalog: Mapping[str, SpotFact]
    last_candidates: tuple[CandidateReference, ...] = ()
    current_itinerary: Itinerary | None = None
    # 優先順(高い順)のティア。値は正規化キー → 該当する spot_id の集合。
    # 集合の要素が2件以上なら、そのティアで「曖昧」と判定する。
    _tier_itinerary: dict[str, set[str]] = field(default_factory=dict, compare=False)
    _tier_candidates: dict[str, set[str]] = field(default_factory=dict, compare=False)
    _tier_name_exact: dict[str, set[str]] = field(default_factory=dict, compare=False)
    # 正式名の部分一致索引: (キー, spot_id) のペア。キーはフルネームの正規化形と
    # 単語(空白区切り)単位の正規化形の両方を含む(2026-08-04、レビュー是正)。
    _name_partial_index: tuple[tuple[str, str], ...] = field(
        default_factory=tuple, compare=False
    )
    _tier_alias_exact: dict[str, set[str]] = field(default_factory=dict, compare=False)
    _alias_partial_index: tuple[tuple[str, str], ...] = field(
        default_factory=tuple, compare=False
    )
    _display_names: dict[str, str] = field(default_factory=dict, compare=False)
    # 序数参照(「2番目」等)の解決先。last_candidates の rank → spot_id。
    _rank_index: dict[int, set[str]] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        display_names = {
            spot_id: spot.name_ja for spot_id, spot in self.spot_catalog.items()
        }
        for candidate in self.last_candidates:
            display_names.setdefault(candidate.spot_id, candidate.name_ja)
        object.__setattr__(self, "_display_names", display_names)

        tier_itinerary: dict[str, set[str]] = {}
        if self.current_itinerary is not None:
            for day in self.current_itinerary.days:
                for spot_id in (
                    day.origin.spot_id,
                    day.destination.spot_id,
                    *(item.spot_id for item in day.items),
                ):
                    name = display_names.get(spot_id)
                    if name:
                        tier_itinerary.setdefault(normalize_name(name), set()).add(spot_id)
        object.__setattr__(self, "_tier_itinerary", tier_itinerary)

        tier_candidates: dict[str, set[str]] = {}
        rank_index: dict[int, set[str]] = {}
        for candidate in self.last_candidates:
            tier_candidates.setdefault(normalize_name(candidate.name_ja), set()).add(
                candidate.spot_id
            )
            rank_index.setdefault(candidate.rank, set()).add(candidate.spot_id)
        object.__setattr__(self, "_tier_candidates", tier_candidates)
        object.__setattr__(self, "_rank_index", rank_index)

        tier_name_exact: dict[str, set[str]] = {}
        tier_alias_exact: dict[str, set[str]] = {}
        name_partial_index: list[tuple[str, str]] = []
        alias_partial_index: list[tuple[str, str]] = []
        for spot_id, spot in self.spot_catalog.items():
            name_key = normalize_name(spot.name_ja)
            if name_key:
                tier_name_exact.setdefault(name_key, set()).add(spot_id)
                name_partial_index.append((name_key, spot_id))
                for token in _tokenize(spot.name_ja):
                    token_key = normalize_name(token)
                    if token_key and token_key != name_key:
                        name_partial_index.append((token_key, spot_id))
            for alias in spot.aliases_ja:
                alias_key = normalize_name(alias)
                if not alias_key:
                    continue
                tier_alias_exact.setdefault(alias_key, set()).add(spot_id)
                alias_partial_index.append((alias_key, spot_id))
                for token in _tokenize(alias):
                    token_key = normalize_name(token)
                    if token_key and token_key != alias_key:
                        alias_partial_index.append((token_key, spot_id))
        object.__setattr__(self, "_tier_name_exact", tier_name_exact)
        object.__setattr__(self, "_tier_alias_exact", tier_alias_exact)
        object.__setattr__(self, "_name_partial_index", tuple(name_partial_index))
        object.__setattr__(self, "_alias_partial_index", tuple(alias_partial_index))

    def resolve(self, name: str) -> str | None:
        """後方互換の単純 API。曖昧・未解決はどちらも `None`。"""

        match = self.resolve_detailed(name)
        return match.spot_id if match.status == "resolved" else None

    def resolve_detailed(self, name: str) -> NameMatch:
        """優先度つきティアを順に見て、最初にヒットしたティアで確定する。

        0. 序数参照(「2番目」等)は `last_candidates` の `rank` から直接解決
           する(2026-08-04、レビュー是正・裁定9)。
        1. 現在の旅程内の名前(完全一致)
        2. 直近候補の名前(完全一致)
        3. 正式名完全一致 > 正式名部分一致 > 別名完全一致 > 別名部分一致
           (かな折りたたみ正規化のうえで。実データの誤名寄せ対策)
        """

        if not name or not name.strip():
            return NameMatch(status="unresolved")
        ordinal_match = self._resolve_ordinal(name)
        if ordinal_match is not None:
            return ordinal_match
        query = normalize_name(name)
        if not query:
            return NameMatch(status="unresolved")
        for tier in (
            self._tier_itinerary,
            self._tier_candidates,
            self._tier_name_exact,
        ):
            hit = tier.get(query)
            if hit:
                return self._outcome(hit)
        if len(query) >= _MIN_PARTIAL_MATCH_LENGTH:
            name_partial_hits = self._partial_hits(self._name_partial_index, query)
            if name_partial_hits:
                return self._outcome(name_partial_hits)
        alias_exact_hit = self._tier_alias_exact.get(query)
        if alias_exact_hit:
            return self._outcome(alias_exact_hit)
        if len(query) >= _MIN_PARTIAL_MATCH_LENGTH:
            alias_partial_hits = self._partial_hits(self._alias_partial_index, query)
            if alias_partial_hits:
                return self._outcome(alias_partial_hits)
        return NameMatch(status="unresolved")

    def _resolve_ordinal(self, name: str) -> NameMatch | None:
        """「2番目」等を `last_candidates` の rank から解決する。

        序数表現ではない、または該当 rank の候補が無ければ `None` を返し、
        呼び出し元は通常の名前ティアへフォールバックする。
        """

        normalized = unicodedata.normalize("NFKC", name.strip())
        match = _ORDINAL_RE.match(normalized)
        if match is None:
            return None
        rank = int(match.group(1))
        hit = self._rank_index.get(rank)
        if not hit:
            return NameMatch(status="unresolved")
        return self._outcome(hit)

    @staticmethod
    def _partial_hits(index: tuple[tuple[str, str], ...], query: str) -> set[str]:
        return {
            spot_id
            for key, spot_id in index
            if len(key) >= _MIN_PARTIAL_MATCH_LENGTH and (key in query or query in key)
        }

    def _outcome(self, spot_ids: set[str]) -> NameMatch:
        if len(spot_ids) == 1:
            return NameMatch(status="resolved", spot_id=next(iter(spot_ids)))
        candidates = tuple(
            sorted(self._display_names.get(spot_id, spot_id) for spot_id in spot_ids)
        )
        return NameMatch(status="ambiguous", candidates=candidates)


def build_name_resolution_context(
    *,
    spot_catalog: Mapping[str, SpotFact],
    last_candidates: Iterable[CandidateReference] = (),
    current_itinerary: Itinerary | None = None,
) -> NameResolutionContext:
    return NameResolutionContext(
        spot_catalog=spot_catalog,
        last_candidates=tuple(last_candidates),
        current_itinerary=current_itinerary,
    )


def resolve_names(
    context: NameResolutionContext, names: Iterable[str]
) -> NameResolutionOutcome:
    """複数名を解決し、解決済み/未解決/曖昧の3つに仕分ける(C4)。"""

    resolved: list[str] = []
    dropped: list[str] = []
    ambiguous: list[str] = []
    for name in names:
        match = context.resolve_detailed(name)
        if match.status == "resolved" and match.spot_id is not None:
            resolved.append(match.spot_id)
        elif match.status == "ambiguous":
            ambiguous.append(describe_ambiguous(name, match))
        else:
            dropped.append(name)
    return NameResolutionOutcome(resolved=resolved, dropped=dropped, ambiguous=ambiguous)


def resolve_constraint_target(context: NameResolutionContext, value: str) -> str:
    """制約 `args` の `target`/`a`/`b` を、名前ならスポット名 → id へ変換する。

    生タグ(述語の意味上、target には生タグも書ける)や、曖昧・未解決だった
    値はそのまま通す(呼び出し元が曖昧を検知したい場合は
    `resolve_constraint_target_detailed` を使う)。
    """

    resolved_value, _ = resolve_constraint_target_detailed(context, value)
    return resolved_value


def resolve_constraint_target_detailed(
    context: NameResolutionContext, value: str
) -> tuple[str, NameMatch]:
    """`value` を解決しつつ、曖昧判定に使える `NameMatch` も返す。

    未解決(`unresolved`)は生タグの可能性があるため元の値をそのまま返す。
    曖昧(`ambiguous`)も値自体は元のまま返す — 呼び出し元がこの制約要素
    ごと落とすかどうかを判断する(itinerary_subagent.py フロー2)。
    """

    match = context.resolve_detailed(value)
    resolved_value = match.spot_id if match.status == "resolved" else value
    return resolved_value, match
