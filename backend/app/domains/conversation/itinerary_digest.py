"""旅程の名前空間ダイジェスト整形器。

`Docs/30_design/agent_react_architecture.md` §5 フロー4・§3.1 ③が仕様。
メインループのコンテキスト③(現在の旅程)と、`plan_itinerary`/`edit_itinerary`
Tool の戻り値(軌跡へ載せる観測)の**両方でこの関数を使う**(実装を1つにする)。

`spot_id` は一切含めない。すべてスポット名(`spot_names`)で表現する。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.domains.conversation.guards import _FORBIDDEN_SPOT_ID_RE
from app.domains.itinerary.types import Diff, Itinerary, Mode

_MODE_JA = {Mode.CAR: "車", Mode.FOOT: "徒歩"}

# 名前解決に失敗した要素の中立表記。id へフォールバックしない
# (2026-08-04、[25 §1-7](../../../../Docs/25_known_issues.md))。
UNNAMED_SPOT_JA = "(名称未登録の地点)"


def _name(spot_names: Mapping[str, str], spot_id: str) -> str:
    return spot_names.get(spot_id, UNNAMED_SPOT_JA)


def mask_spot_ids(text: str, spot_names: Mapping[str, str]) -> str:
    """文中の `spot_` トークンを表示名(引けなければ中立表記)へ置換する。

    譲歩文(`Concession.message_ja`)の唯一の生成点(`predicates.py`)は表示名で
    メッセージを組むが、旧形式(spot_id 入り)で永続化済みの版が undo/GET で
    再浮上する経路への防御として、整形・送出層でも同じ変換をかける
    ([25 §1-7](../../../../Docs/25_known_issues.md))。正規表現は
    `guards._FORBIDDEN_SPOT_ID_RE` と同一パターンを共有する(重複定義しない)。
    """

    return _FORBIDDEN_SPOT_ID_RE.sub(
        lambda match: spot_names.get(match.group(0), UNNAMED_SPOT_JA), text
    )


def mask_concession_list(
    concessions: Sequence[Mapping[str, Any]], spot_names: Mapping[str, str]
) -> list[dict[str, Any]]:
    """Concession 辞書列の `message_ja` をまとめてマスクする(送出層の共通処理)。

    `state:itinerary`/undo・redo・`GET /api/v1/itinerary` の送出層
    (`tool_adapters.py` / `api/routers/itinerary.py`)が使う。
    """

    masked: list[dict[str, Any]] = []
    for concession in concessions:
        item = dict(concession)
        message = item.get("message_ja")
        if isinstance(message, str):
            item["message_ja"] = mask_spot_ids(message, spot_names)
        masked.append(item)
    return masked


def _fmt_minute(value: int) -> str:
    hour, minute = divmod(max(0, value), 60)
    return f"{hour:02d}:{minute:02d}"


def format_itinerary_digest(
    itinerary: Itinerary | None,
    *,
    spot_names: Mapping[str, str],
    diff: Diff | None = None,
    dropped: Sequence[str] = (),
    ambiguous: Sequence[str] = (),
    notes: str | None = None,
) -> str:
    """日ごとの出発時刻・各スポット名・到着/滞在/移動・終了時刻・譲歩・diff・

    落とした要素・曖昧だった要素(候補つき)を、短い日本語テキストにする。
    `spot_id` は出さない。`dropped` は完全に解決できなかった要素、
    `ambiguous` は複数候補に解けた要素(`name_resolution.describe_ambiguous`
    で候補つきに整形済みの文字列)を渡す(§5 フロー4)。
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
                lines.append(f"  ・{mask_spot_ids(concession.message_ja, spot_names)}")
        if itinerary.assumptions:
            lines.append("仮の前提: " + "、".join(itinerary.assumptions))

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
    if ambiguous:
        lines.append("曖昧だった項目: " + "、".join(ambiguous))
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
