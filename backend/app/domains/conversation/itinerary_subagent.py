"""旅程計画サブエージェント(完全ワークフロー。§5)。

`Docs/30_design/agent_react_architecture.md` §5 が仕様。`plan_itinerary`/
`edit_itinerary` の2つの Tool を、内部に自律ループを持たない4フロー固定順で
処理する:

1. **受付** — メインエージェントが書いた引数(`days`/`must_visit`/`ops`/
   `constraints`/`notes`。POI はスポット名)を受け取る。契約は変えない。
2. **構築** — 名寄せ(`name_resolution.py` の本実装)でスポット名を `spot_id`
   に解決し、既存 `constraints`(現行 version から継承)と新規 add/remove を
   マージする。解決できない・曖昧な要素は要素単位で落とす(C4)。
3. **実行** — 既存 Tool(`tool_adapters.ToolAdapters`。内部は変更しない)を
   呼ぶ。revert 特例・ops 適用・ILS×3・解選択・OSRM leg 経路・
   `state:itinerary` の送出はそちら側の責務のまま。
4. **整形** — `itinerary_digest.format_itinerary_digest` で自然文へ整形する
   (譲歩・diff・落とした要素・曖昧だった要素を含む)。

メインエージェントは `spot_id` を一切見ない(§3.3)。この層が
「メインエージェントが書いたスポット名」⇔「既存 Tool の spot_id 契約」の
橋渡しを行う。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from app.domains.conversation.guards import (
    normalize_revert_ops,
    validate_and_normalize_constraints,
)
from app.domains.conversation.itinerary_digest import format_itinerary_digest
from app.domains.conversation.name_resolution import (
    NameResolutionContext,
    build_name_resolution_context,
    describe_ambiguous,
    resolve_constraint_target_detailed,
    resolve_names,
)
from app.domains.conversation.recommendation_context import build_recommendation_context
from app.domains.conversation.state import DegradedState, ItineraryState, TurnState
from app.domains.conversation.tool_ports import ConversationToolPort
from app.domains.conversation.types import (
    ConstraintDraft,
    EditItineraryArgs,
    MainConstraintOps,
    MainEditItineraryArgs,
    MainPlanItineraryArgs,
    PlanItineraryArgs,
    ToolError,
    ToolErrorCode,
    ToolResult,
)
from app.domains.itinerary.types import Diff, Itinerary


async def run_plan_itinerary(
    state: TurnState,
    tools: ConversationToolPort,
    raw_args: dict[str, Any],
    step_id: int,
) -> tuple[str, dict[str, Any] | None]:
    """`plan_itinerary` Tool。フロー1〜4を固定順で実行する。"""

    # フロー1: 受付(契約は変えない。POI はスポット名で来る)。
    parsed = MainPlanItineraryArgs.model_validate(raw_args)

    # フロー2: 構築。
    name_context = _name_context(state)
    dropped: list[str] = []
    ambiguous: list[str] = []

    days: list[dict[str, Any]] = []
    # 日の起終点(2026-08-04 夜、ADR-0022 追記・レビュー是正 H-2): ソルバーは
    # 起終点を挿入対象から常に除外するため、名寄せが起終点にしか解決しない
    # ケース(例: origin_name と must_visit の両方に同じ地点名を書く)を
    # 検知するために全 day の起終点 spot_id を集める。
    endpoint_ids: set[str] = set()
    for day in parsed.days:
        origin_id = _resolve_endpoint(
            name_context, day.origin_name, state.default_origin_spot_id, dropped, ambiguous
        )
        if origin_id is None:
            # 2026-08-04([25 §1-4]の是正。Docs/30_design/
            # agent_react_architecture.md §5): メインが origin_name を渡さず、
            # 既存旅程の起点(state.default_origin_spot_id)も無いときは、
            # 勝手に施設を選ばず precondition_unmet を返す。メインエージェント
            # は ask_user で聞くか、会話から得た地点名を明示して再実行する
            # (その仮定は assumptions に書く)。
            error = ToolError(
                code=ToolErrorCode.PRECONDITION_UNMET,
                message_ja=(
                    "起点が未指定です。ユーザーに尋ねるか、会話に出た地点名を"
                    "origin に指定してください(仮定した場合は assumptions に"
                    "書くこと)"
                ),
                recoverable=True,
            )
            return error.message_ja, _error_payload(error)
        destination_id = _resolve_endpoint(
            name_context, day.destination_name, origin_id, dropped, ambiguous
        )
        endpoint_ids.add(origin_id)
        if destination_id is not None:
            endpoint_ids.add(destination_id)
        days.append(
            {
                "date": day.date,
                "start": day.start,
                "end": day.end,
                "origin": _endpoint_dict(state, origin_id),
                "destination": _endpoint_dict(state, destination_id),
            }
        )
    must_visit_outcome = resolve_names(name_context, parsed.must_visit)
    dropped.extend(must_visit_outcome.dropped)
    ambiguous.extend(must_visit_outcome.ambiguous)
    # 挿入してもしなくてもよい候補(2026-08-04 夜、ADR-0022)。must_visit と
    # 同じ名寄せ経路で解決する。require 制約は作らない(ItineraryService
    # 側で must_visit とは別に insertion_pool へのみ加える)。
    candidate_spots_outcome = resolve_names(name_context, parsed.candidate_spots)
    dropped.extend(candidate_spots_outcome.dropped)
    ambiguous.extend(candidate_spots_outcome.ambiguous)

    used_ids = set(active_constraint_ids(state))
    constraints, constraints_dropped, constraints_ambiguous = _build_constraint_drafts(
        name_context,
        parsed.constraints,
        spots=state.spot_catalog,
        created_at_version=1,
        used_ids=used_ids,
    )
    dropped.extend(constraints_dropped)
    ambiguous.extend(constraints_ambiguous)
    if parsed.constraints is not None and parsed.constraints.remove:
        dropped.append(
            "旅程がまだ無いため制約の解除は無視しました: "
            + "、".join(parsed.constraints.remove)
        )

    if not days:
        error = ToolError(
            code=ToolErrorCode.REFERENCE_UNRESOLVED,
            message_ja="旅程の日程が指定されていません。",
            recoverable=True,
        )
        return error.message_ja, _error_payload(error)

    # 2026-08-04 夜(ADR-0022 追記・レビュー是正 H-2): 空判定は「実効プール」
    # (解決済み must_visit ∪ candidate_spots から日の起終点を引いた集合)で
    # 行う。ソルバーは起終点を挿入対象から常に除外するため、名寄せが起終点
    # にしか解決しないケース(例: origin_name と must_visit の両方に同じ
    # 地点名を書く)を見逃さない。空の旅程を黙って返さず precondition_unmet
    # で止める。メインエージェントはこれを見て recommend を呼ぶか ask_user
    # で聞くかを選べる(起点未解決と同型のガードレール)。
    resolved_pool = frozenset(must_visit_outcome.resolved) | frozenset(
        candidate_spots_outcome.resolved
    )
    effective_pool = resolved_pool - endpoint_ids
    if not effective_pool:
        # M-1(レビュー是正): 「指定されていません」だけでは、メインが
        # 「書いたのに指定されていないと言われた」状態になり、同じ名前での
        # 再試行や不要な recommend を誘発する。落ちた要素・曖昧だった要素・
        # 起終点との衝突を理由として付記する。
        notes: list[str] = []
        if resolved_pool and resolved_pool <= endpoint_ids:
            notes.append("指定された場所が起点・終点と同じです")
        if dropped:
            notes.append("解決できなかった項目: " + "、".join(dropped))
        if ambiguous:
            notes.append("曖昧だった項目: " + "、".join(ambiguous))
        message = (
            "旅程に含める観光地が指定されていません。recommend で候補を"
            "挙げるか、行きたい場所を確認してください"
        )
        if notes:
            message += "(" + "。".join(notes) + ")"
        error = ToolError(
            code=ToolErrorCode.PRECONDITION_UNMET,
            message_ja=message,
            recoverable=True,
        )
        return error.message_ja, _error_payload(error)

    # フロー3: 実行(既存 Tool。内部は変更しない)。
    selection_text = parsed.notes or ""
    context = build_recommendation_context(state)
    result = await tools.plan_itinerary(
        step_id=step_id,
        user_id=state.user_id,
        args=PlanItineraryArgs(
            days=days,
            must_visit=must_visit_outcome.resolved,
            candidate_spots=candidate_spots_outcome.resolved,
            assumptions=parsed.assumptions,
        ),
        constraints=constraints,
        selection_text=selection_text,
        recommendation_context=context,
        use_specialist=bool(selection_text.strip()),
    )
    if isinstance(result, ToolError):
        return result.message_ja, _error_payload(result)
    state.step_results[step_id] = result
    _apply_itinerary_result(state, result)

    # フロー4: 整形。
    digest = _format_itinerary_result_digest(
        state, result, dropped=dropped, ambiguous=ambiguous
    )
    return digest, None


async def run_edit_itinerary(
    state: TurnState,
    tools: ConversationToolPort,
    raw_args: dict[str, Any],
    step_id: int,
) -> tuple[str, dict[str, Any] | None]:
    """`edit_itinerary` Tool。フロー1〜4を固定順で実行する。"""

    if state.itinerary is None:
        return _edit_without_itinerary(state, raw_args)

    # フロー1: 受付。
    parsed = MainEditItineraryArgs.model_validate(raw_args)

    # フロー2: 構築。
    name_context = _name_context(state)
    dropped: list[str] = []
    ambiguous: list[str] = []

    resolved_ops, ops_dropped, ops_ambiguous = _resolve_ops(name_context, parsed.ops)
    dropped.extend(ops_dropped)
    ambiguous.extend(ops_ambiguous)
    # revert 特例: 他 op と混在していたら、revert だけを残し混在した事実を報告する
    # (§5 フロー3。旧実装は `changed` フラグを握りつぶしていた)。
    resolved_ops, mixed_with_revert = normalize_revert_ops(resolved_ops)
    if mixed_with_revert:
        dropped.append(
            "元に戻す操作は他の操作と同時に指定できないため、"
            "元に戻す操作だけを実行しました。"
        )
    if not resolved_ops and parsed.ops:
        error = ToolError(
            code=ToolErrorCode.REFERENCE_UNRESOLVED,
            message_ja="指定されたスポット名をすべて解決できませんでした。",
            recoverable=True,
            details={"dropped": dropped, "ambiguous": ambiguous},
        )
        return error.message_ja, _error_payload(error)

    active_ids = set(active_constraint_ids(state))
    remove_ids = list(parsed.constraints.remove) if parsed.constraints is not None else []
    valid_remove = [value for value in remove_ids if value in active_ids]
    invalid_remove = [value for value in remove_ids if value not in active_ids]
    if invalid_remove:
        dropped.append("現在有効な制約 id ではありません: " + "、".join(invalid_remove))

    constraints, constraints_dropped, constraints_ambiguous = _build_constraint_drafts(
        name_context,
        parsed.constraints,
        spots=state.spot_catalog,
        created_at_version=state.itinerary.version + 1,
        used_ids=active_ids - set(valid_remove),
    )
    dropped.extend(constraints_dropped)
    ambiguous.extend(constraints_ambiguous)

    # フロー3: 実行(既存 Tool。revert 特例・ops 適用・ILS×3・解選択・
    # OSRM leg 経路・state:itinerary の送出はすべて向こう側の責務)。
    selection_text = parsed.notes or ""
    context = build_recommendation_context(state)
    result = await tools.edit_itinerary(
        step_id=step_id,
        user_id=state.user_id,
        args=EditItineraryArgs(
            ops=resolved_ops,
            allow_refill=parsed.allow_refill,
            assumptions=parsed.assumptions,
        ),
        constraints=constraints,
        constraints_remove=valid_remove,
        selection_text=selection_text,
        recommendation_context=context,
        use_specialist=bool(selection_text.strip()),
    )
    if isinstance(result, ToolError):
        return result.message_ja, _error_payload(result)
    state.step_results[step_id] = result
    _apply_itinerary_result(state, result)

    # フロー4: 整形。
    digest = _format_itinerary_result_digest(
        state, result, dropped=dropped, ambiguous=ambiguous
    )
    return digest, None


def _edit_without_itinerary(
    state: TurnState, raw_args: dict[str, Any]
) -> tuple[str, dict[str, Any] | None]:
    """旅程がまだ無い `edit_itinerary`(§5「旅程が無い状態の edit_itinerary は

    ToolError(precondition_unmet) を結果として返す」)。

    2026-08-04 レビュー是正(High): ReAct 化で `constraints` は常に
    `plan_itinerary`/`edit_itinerary` の引数として来るため、**この経路が
    `threads.pending_constraints` へ書き込める新アーキ上で唯一の場所**に
    なった。`constraints.add` があれば名前解決・検証まで済ませたうえで
    `state.pending_constraints` へ積む(読出し・消去 = 最初の `plan_itinerary`
    での移送は既存のまま。data_model.md §4.5.4)。
    """

    try:
        parsed = MainEditItineraryArgs.model_validate(raw_args)
    except ValidationError:
        parsed = None

    saved = False
    if parsed is not None and parsed.constraints is not None and parsed.constraints.add:
        name_context = _name_context(state)
        used_ids = set(active_constraint_ids(state))
        drafts, _dropped, _ambiguous = _build_constraint_drafts(
            name_context,
            parsed.constraints,
            spots=state.spot_catalog,
            created_at_version=1,
            used_ids=used_ids,
        )
        if drafts:
            state.pending_constraints = [
                *state.pending_constraints,
                *[value.model_dump(mode="json", exclude_none=True) for value in drafts],
            ]
            saved = True

    message = "編集できる旅程がまだありません。先に plan_itinerary を使ってください。"
    if saved:
        message += "指定された条件は保存済みです。旅程作成時に反映します。"
    error = ToolError(
        code=ToolErrorCode.PRECONDITION_UNMET,
        message_ja=message,
        recoverable=True,
    )
    return error.message_ja, _error_payload(error)


def active_constraint_ids(state: TurnState) -> list[str]:
    """現在有効な制約 id の列(guided schema の remove enum・使用済み id の判定に使う)。"""

    active = (
        state.itinerary.constraints
        if state.itinerary is not None
        else state.pending_constraints
    )
    return [
        str(value["id"])
        for value in active
        if isinstance(value.get("id"), str) and value["id"]
    ]


# ---------------------------------------------------------------------------
# フロー2: 名前解決の補助
# ---------------------------------------------------------------------------


def _name_context(state: TurnState) -> NameResolutionContext:
    return build_name_resolution_context(
        spot_catalog=state.spot_catalog,
        last_candidates=state.last_candidates,
        current_itinerary=state.itinerary.itinerary if state.itinerary is not None else None,
    )


def _resolve_endpoint(
    name_context: NameResolutionContext,
    name: str | None,
    fallback_spot_id: str | None,
    dropped: list[str],
    ambiguous: list[str],
) -> str | None:
    if name is None:
        return fallback_spot_id
    match = name_context.resolve_detailed(name)
    if match.status == "resolved":
        return match.spot_id
    if match.status == "ambiguous":
        ambiguous.append(describe_ambiguous(name, match))
    else:
        dropped.append(name)
    return fallback_spot_id


def _endpoint_dict(state: TurnState, spot_id: str | None) -> dict[str, Any]:
    if spot_id is None:
        return {"kind": "spot", "id": ""}
    spot = state.spot_catalog.get(spot_id)
    kind = "facility" if spot is not None and spot.kind == "facility" else "spot"
    return {"kind": kind, "id": spot_id}


def _resolve_ops(
    name_context: NameResolutionContext, ops: Sequence[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    dropped: list[str] = []
    ambiguous: list[str] = []
    resolved: list[dict[str, Any]] = []
    for raw in ops:
        operation = dict(raw)
        op = operation.get("op")
        if op in {"add", "remove", "lock"}:
            names = operation.get("targets") or []
            names_list = names if isinstance(names, list) else [names]
            outcome = resolve_names(
                name_context, (value for value in names_list if isinstance(value, str))
            )
            dropped.extend(outcome.dropped)
            ambiguous.extend(outcome.ambiguous)
            if not outcome.resolved:
                continue
            operation["targets"] = outcome.resolved
            if op == "add":
                # 2026-08-04 レビュー是正(High): `after` もスポット名→id
                # 解決の対象にする。`targets` だけ解決して `after` を素通り
                # させると、下流の `_add_position`/`_require_item` が
                # spot_id しか受け付けないため「地点Bを地点Aの後に追加」が
                # 常に失敗していた。未解決なら `after` だけを落として報告し、
                # 手全体は実行する(C4)。
                after_name = operation.get("after")
                if isinstance(after_name, str) and after_name.strip():
                    after_match = name_context.resolve_detailed(after_name)
                    if after_match.status == "resolved":
                        operation["after"] = after_match.spot_id
                    else:
                        if after_match.status == "ambiguous":
                            ambiguous.append(
                                f"after: {describe_ambiguous(after_name, after_match)}"
                            )
                        else:
                            dropped.append(f"after: {after_name}")
                        operation["after"] = None
        elif op in {"move", "set_stay", "set_time"}:
            name = operation.get("target")
            if not isinstance(name, str):
                continue
            match = name_context.resolve_detailed(name)
            if match.status == "resolved":
                operation["target"] = match.spot_id
            else:
                if match.status == "ambiguous":
                    ambiguous.append(describe_ambiguous(name, match))
                else:
                    dropped.append(name)
                continue
        elif op == "replace":
            target_name = operation.get("target")
            with_name = operation.get("with")
            ok = True
            for key, value in (("target", target_name), ("with", with_name)):
                if not isinstance(value, str):
                    ok = False
                    continue
                match = name_context.resolve_detailed(value)
                if match.status == "resolved":
                    operation[key] = match.spot_id
                else:
                    ok = False
                    if match.status == "ambiguous":
                        ambiguous.append(describe_ambiguous(value, match))
                    else:
                        dropped.append(value)
            if not ok:
                continue
        elif op == "revert":
            pass
        else:  # pragma: no cover - guided decoding が enum で防ぐ
            continue
        resolved.append(operation)
    return resolved, dropped, ambiguous


def _build_constraint_drafts(
    name_context: NameResolutionContext,
    ops: MainConstraintOps | None,
    *,
    spots: Any,
    created_at_version: int,
    used_ids: set[str],
) -> tuple[list[ConstraintDraft], list[str], list[str]]:
    """メインエージェントが書いた制約を、名前解決 + 検証まで済ませる。

    ここで id を確定させておくことで、Tool 実行後に `state.itinerary.constraints`
    (会話状態側の表示用コピー)を組み立て直せる(既存 Tool 実装が内部で
    行う正規化と、id の割り当てロジックは同一 = `validate_and_normalize_constraints`。
    現行 version から継承する既存 constraints とのマージそのものは
    呼び出し元(`run_plan_itinerary`/`run_edit_itinerary`)が
    `state.itinerary.constraints`/`state.pending_constraints` を土台にして行う)。
    """

    if ops is None or not ops.add:
        return [], [], []
    drafts: list[ConstraintDraft] = []
    dropped: list[str] = []
    ambiguous: list[str] = []
    for item in ops.add:
        args = dict(item.args)
        ambiguous_note: str | None = None
        for key in ("target", "a", "b"):
            value = args.get(key)
            if not isinstance(value, str):
                continue
            resolved_value, match = resolve_constraint_target_detailed(name_context, value)
            if match.status == "ambiguous":
                ambiguous_note = describe_ambiguous(value, match)
                break
            args[key] = resolved_value
        if ambiguous_note is not None:
            ambiguous.append(f"{item.pred}: {ambiguous_note}")
            continue
        drafts.append(
            ConstraintDraft(
                pred=item.pred,
                args=args,
                weight=item.weight,
                source_text=item.source_text,
            )
        )
    guard_result = validate_and_normalize_constraints(
        drafts,
        spots,
        created_at_version=created_at_version,
        used_ids=used_ids,
    )
    dropped.extend(
        f"{value.text}: {value.reason}" if value.reason else value.text
        for value in guard_result.unmodeled
    )
    return list(guard_result.constraints), dropped, ambiguous


# ---------------------------------------------------------------------------
# フロー3の結果の適用(state.itinerary の更新)
# ---------------------------------------------------------------------------


def _apply_itinerary_result(state: TurnState, result: ToolResult) -> None:
    """DB へ保存された制約(Service が返す `constraints`)をそのまま採用する。

    2026-08-04 レビュー是正(High・裁定7): 以前はメインループが渡した制約
    だけから `state.itinerary.constraints` を再構成しており、Service が
    plan/edit 時に再採番した id・`must_visit`/`add`/`set_time` 由来の暗黙
    制約・revert 先の版の制約が欠落しうった。`PlanItineraryResult`/
    `EditItineraryResult.constraints` を正として使うことで、DB に保存された
    制約と同一ターンの `state.itinerary.constraints` が常に一致する。
    """

    raw = result.data.get("itinerary")
    if isinstance(raw, dict):
        itinerary = Itinerary.model_validate(raw)
        raw_constraints = result.data.get("constraints")
        constraints = raw_constraints if isinstance(raw_constraints, list) else []
        state.itinerary = ItineraryState(
            itinerary=itinerary,
            constraints=constraints,
            parent_version=(state.itinerary.version if state.itinerary is not None else None),
        )
    for code in result.degraded:
        state.degraded.append(
            DegradedState(
                code=code,
                stage=result.tool.value,
                message=f"{result.tool.value} は縮退経路を使用しました",
            )
        )


# ---------------------------------------------------------------------------
# フロー4: 整形
# ---------------------------------------------------------------------------


def _format_itinerary_result_digest(
    state: TurnState,
    result: ToolResult,
    *,
    dropped: list[str],
    ambiguous: list[str],
) -> str:
    itinerary = Itinerary.model_validate(result.data["itinerary"])
    diff_raw = result.data.get("diff")
    diff = Diff.model_validate(diff_raw) if isinstance(diff_raw, dict) else None
    unmodeled = result.data.get("unmodeled", []) or []
    combined_dropped = [*dropped]
    for value in unmodeled:
        if isinstance(value, dict) and value.get("reason"):
            label = value.get("source_text") or value.get("pred")
            combined_dropped.append(f"{label}: {value['reason']}")
    return format_itinerary_digest(
        itinerary,
        spot_names=state.spot_names,
        diff=diff,
        dropped=combined_dropped,
        ambiguous=ambiguous,
    )


def _error_payload(error: ToolError) -> dict[str, Any]:
    return {
        "code": error.code.value,
        "message_ja": error.message_ja,
        "recoverable": error.recoverable,
    }
