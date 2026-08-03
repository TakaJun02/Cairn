"""旅程の名前空間ダイジェスト整形器。

`Docs/30_design/agent_react_architecture.md` §5 フロー4・§3.1 ③が仕様。
メインループのコンテキスト③(現在の旅程)と、`plan_itinerary`/`edit_itinerary`
Tool の戻り値(軌跡へ載せる観測)の**両方でこの関数を使う**(実装を1つにする)。

`spot_id` は一切含めない。すべてスポット名(`spot_names`)で表現する。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.domains.itinerary.types import Diff, Itinerary, Mode

_MODE_JA = {Mode.CAR: "車", Mode.FOOT: "徒歩"}


def _name(spot_names: Mapping[str, str], spot_id: str) -> str:
    return spot_names.get(spot_id, spot_id)


def _fmt_minute(value: int) -> str:
    hour, minute = divmod(max(0, value), 60)
    return f"{hour:02d}:{minute:02d}"


def format_itinerary_digest(
    itinerary: Itinerary | None,
    *,
    spot_names: Mapping[str, str],
    diff: Diff | None = None,
    dropped: Sequence[str] = (),
    notes: str | None = None,
) -> str:
    """日ごとの出発時刻・各スポット名・到着/滞在/移動・終了時刻・譲歩・diff・

    落とした要素を、短い日本語テキストにする。`spot_id` は出さない。
    """

    if itinerary is None or not itinerary.days:
        lines = ["現在の旅程はまだありません。"]
    else:
        lines = [f"旅程(version {itinerary.version}):"]
        for day_index, day in enumerate(itinerary.days, 1):
            lines.append(
                f"{day_index}日目({day.date}) "
                f"出発{_fmt_minute(day.start_min)} 起点:{_name(spot_names, day.origin.spot_id)}"
            )
            for item in day.items:
                mode_ja = _MODE_JA.get(item.leg_from_prev.mode, str(item.leg_from_prev.mode))
                lock = " [固定]" if item.locked else ""
                lines.append(
                    f"  ・{_name(spot_names, item.spot_id)}: "
                    f"到着{_fmt_minute(item.arrive_min)} 滞在{item.stay_min}分 "
                    f"出発{_fmt_minute(item.depart_min)}"
                    f"(移動 {mode_ja}{item.leg_from_prev.min}分){lock}"
                )
            destination_name = _name(spot_names, day.destination.spot_id)
            lines.append(f"  終了{_fmt_minute(day.end_min)} 終点:{destination_name}")
        if itinerary.concessions:
            lines.append("譲歩:")
            for concession in itinerary.concessions:
                lines.append(f"  ・{concession.message_ja}")

    if diff is not None and (diff.added or diff.removed or diff.moved or diff.retimed):
        lines.append("今回の変更:")
        if diff.added:
            lines.append("  追加: " + "、".join(_name(spot_names, value) for value in diff.added))
        if diff.removed:
            lines.append("  削除: " + "、".join(_name(spot_names, value) for value in diff.removed))
        if diff.moved:
            lines.append(
                "  移動: " + "、".join(_name(spot_names, value.spot_id) for value in diff.moved)
            )
        if diff.retimed:
            lines.append(
                "  時刻変更: " + "、".join(_name(spot_names, value) for value in diff.retimed)
            )
    if dropped:
        lines.append("解決できなかった項目: " + "、".join(dropped))
    if notes:
        lines.append(f"補足: {notes}")
    return "\n".join(lines)


def translate_constraint_args(
    args: Mapping[str, Any], *, spot_names: Mapping[str, str]
) -> dict[str, Any]:
    """制約 `args` の `target`/`a`/`b` が spot_id なら、表示用にスポット名へ

    差し替える(メインエージェントの目に spot_id を触れさせないため)。
    実際に永続化する値は元の spot_id のままで、この関数は表示専用。
    """

    translated = dict(args)
    for key in ("target", "a", "b"):
        value = translated.get(key)
        if isinstance(value, str) and value in spot_names:
            translated[key] = spot_names[value]
    return translated


def format_active_constraints(
    constraints: Sequence[Mapping[str, Any]], *, spot_names: Mapping[str, str]
) -> list[dict[str, Any]]:
    """現在有効な制約を、id つき・spot_id を含まない形にする(§5)。"""

    result: list[dict[str, Any]] = []
    for value in constraints:
        result.append(
            {
                "id": value.get("id"),
                "pred": value.get("pred"),
                "args": translate_constraint_args(
                    value.get("args", {}) or {}, spot_names=spot_names
                ),
                "weight": value.get("weight"),
                "source_text": value.get("source_text", ""),
            }
        )
    return result
