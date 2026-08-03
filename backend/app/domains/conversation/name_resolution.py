"""スポット名 → spot_id の名前解決(段2の暫定実装)。

`Docs/30_design/agent_react_architecture.md` はメインエージェントが
`spot_id` を一切見ない・書かないことを要求する(§3.3)。旅程系 Tool
アダプタは、メインエージェントが書いたスポット名を、当面は次の優先順で
`spot_id` に解決する(段4で名寄せ辞書・別名照合へ発展させる前提の
「動く最小限」):

1. 直近候補(`last_candidates`)の `name_ja` 完全一致
2. 現在の旅程に含まれるスポットの `name_ja` 完全一致
3. DB 全体(`spot_catalog`)の `name_ja` 完全一致

解決できない名前は要素単位で落とし、`dropped` に集めて呼び出し元へ返す
(ToolError にはしない。C4: 部分不正は要素単位で落とす)。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from app.domains.conversation.state import CandidateReference, SpotFact
from app.domains.itinerary.types import Itinerary


@dataclass(frozen=True, slots=True)
class NameResolutionContext:
    """1 回の Tool 呼び出しの間だけ使う、名前解決用の索引。"""

    spot_catalog: Mapping[str, SpotFact]
    last_candidates: tuple[CandidateReference, ...] = ()
    current_itinerary: Itinerary | None = None
    _index: dict[str, str] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        index: dict[str, str] = {}
        # 優先順は「後から書いたほうが勝つ」を使い、優先度の低い順に積む。
        for spot_id, spot in self.spot_catalog.items():
            index.setdefault(spot.name_ja, spot_id)
            for alias in spot.aliases_ja:
                index.setdefault(alias, spot_id)
        if self.current_itinerary is not None:
            for day in self.current_itinerary.days:
                for spot_id in (
                    day.origin.spot_id,
                    day.destination.spot_id,
                    *(item.spot_id for item in day.items),
                ):
                    name = self.spot_catalog.get(spot_id)
                    if name is not None:
                        index[name.name_ja] = spot_id
        for candidate in self.last_candidates:
            index[candidate.name_ja] = candidate.spot_id
        object.__setattr__(self, "_index", index)

    def resolve(self, name: str) -> str | None:
        return self._index.get(name)


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


def resolve_spot_name(context: NameResolutionContext, name: str) -> str | None:
    return context.resolve(name)


def resolve_spot_names(
    context: NameResolutionContext, names: Iterable[str]
) -> tuple[list[str], list[str]]:
    """複数名を解決する。戻り値は (解決できた spot_id の列, 解決できなかった名前の列)。"""

    resolved: list[str] = []
    dropped: list[str] = []
    for name in names:
        spot_id = context.resolve(name)
        if spot_id is None:
            dropped.append(name)
        else:
            resolved.append(spot_id)
    return resolved, dropped


def resolve_constraint_target(context: NameResolutionContext, value: str) -> str:
    """制約 `args` の `target`/`a`/`b` を、名前ならスポット名 → id へ変換する。

    生タグ(述語の意味上、target には生タグも書ける)や、既に spot_id で
    書かれた値はそのまま通す。名前解決できた場合だけ書き換える。
    """

    resolved = context.resolve(value)
    return resolved if resolved is not None else value
