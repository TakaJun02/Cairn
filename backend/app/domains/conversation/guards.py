"""コードが強制するガード群(`Docs/30_design/agent_react_architecture.md` §10)。

- `validate_and_normalize_constraints`: メインエージェントが直接書く制約
  (述語 DSL 17 種)の検証。`plan_itinerary`/`edit_itinerary` アダプタが使う
- `normalize_revert_ops`: `edit_itinerary.ops` に `revert` が混じったときの
  排他化。メインループが Tool 実行前に使う
- `validate_response_spot_names`: `respond` のクローズドワールド検査
- `find_forbidden_internal_terms`: `respond` の内部語・自己言及の事後検査
  (層 1 の安全網。`Docs/30_design/dialogue_style.md` §3 論点 E・§4)
- `evaluate_ask_user`(R4・A1・A3〜A6): `ask_user` の質の規律ガード。
  `ask_execution.execute_ask_user` がメイン・レコメンド SA・知識検索 SA の
  3 経路共通で呼ぶ(§7・§10)。旧 A2(質問ターンの連続制限)は
  2026-08-06 に廃止した([ADR-0024](../../../../Docs/adr/0024-ask-user-proactive-hitl.md))
- `filter_unresolvable_ask_user_options`(A7): `ask_user` の選択肢を送出前に
  名寄せし、解決できない選択肢だけを除去する。`tool_adapters.ToolAdapters
  .ask_user` が「送出」の直前(SSE イベント・`pending_ask` 書き込みより前)
  で呼ぶ(`Docs/30_design/dialogue_style.md` §3 論点 C・2026-08-04 決定)
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from app.domains.conversation.state import ProfileState, SpotFact
from app.domains.conversation.types import (
    AskUserArgs,
    AskUserOption,
    ConstraintDraft,
    Slot,
    UnmodeledItem,
)
from app.domains.itinerary.predicates import normalize_constraints
from app.domains.recommendation.types import PreferenceKey

# R4: `ask_user` は 1 ターン 6 回まで(メイン・SA 合算。§3.5・§10)。
# UX 目標値ではなく暴走時の安全弁(2026-08-06 改訂、ADR-0024)。
MAX_ASK_USER_PER_TURN = 6


def has_repeated_ngram(
    text: str,
    *,
    ngram_size: int = 8,
    repeat_threshold: int = 6,
) -> bool:
    """連続する同一 token n-gram と文字列ブロックの双方を検知する。

    `update_profile`/`main_agent`/`respond` の guided/ストリーミング生成で
    共通に使う暴走検知(vLLM が稀に同一断片を無限反復するケースへの安全弁)。
    """

    tokens = re.findall(r"[\w一-龥ぁ-んァ-ヶー]+|[^\w\s]", text)
    if len(tokens) >= ngram_size * repeat_threshold:
        for start in range(len(tokens) - ngram_size * repeat_threshold + 1):
            block = tokens[start : start + ngram_size]
            if all(
                tokens[start + offset * ngram_size : start + (offset + 1) * ngram_size]
                == block
                for offset in range(1, repeat_threshold)
            ):
                return True
    # 空白のない日本語や JSON 断片も拾う。短い `{}` 等は誤検知しない。
    return re.search(r"(.{8,128}?)\1{5,}", text, flags=re.DOTALL) is not None


@dataclass(frozen=True, slots=True)
class GuardResult:
    accepted: bool
    rule: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ConstraintGuardResult:
    constraints: tuple[ConstraintDraft, ...]
    unmodeled: tuple[UnmodeledItem, ...]


def validate_and_normalize_constraints(
    values: Sequence[ConstraintDraft],
    spots: Mapping[str, SpotFact],
    *,
    created_at_version: int,
    used_ids: Iterable[str] = (),
) -> ConstraintGuardResult:
    """17 述語と実在引数を検査し、無効値は `unmodeled` へ移す(C4)。"""

    normalized = normalize_constraints(
        [value.model_dump(mode="python", exclude_none=True) for value in values],
        spots,
        created_at_version=max(1, created_at_version),
        used_ids=used_ids,
    )
    constraints = tuple(
        ConstraintDraft.model_validate(value.model_dump(mode="python"))
        for value in normalized.constraints
    )
    unmodeled = tuple(
        UnmodeledItem(
            text=value.source_text or f"{value.pred}: {value.args}",
            reason=value.reason,
        )
        for value in normalized.unmodeled
    )
    return ConstraintGuardResult(constraints=constraints, unmodeled=unmodeled)


def normalize_revert_ops(ops: Sequence[dict[str, object]]) -> tuple[list[dict[str, object]], bool]:
    """`revert` が混在したら、それ以外を捨てる。"""

    reverts = [value for value in ops if value.get("op") == "revert"]
    if not reverts:
        return [dict(value) for value in ops], False
    return [dict(reverts[0])], len(ops) != 1 or len(reverts) != 1


def validate_response_spot_names(
    text: str,
    *,
    all_spot_names: Mapping[str, str],
    allowed_spot_ids: set[str],
) -> GuardResult:
    """DB 上の未提示 POI 名を `respond` が追加していないか照合する。"""

    unauthorized = sorted(
        name
        for spot_id, name in all_spot_names.items()
        if spot_id not in allowed_spot_ids and name and name in text
    )
    if unauthorized:
        return GuardResult(
            False,
            "closed_world_response",
            f"未提示の POI 名が応答に含まれます: {unauthorized}",
        )
    return GuardResult(True)


# dialogue_style.md §3 論点 E・§4「必須規則」: 内部の実装語(ツール名・
# 処理ステップ名・spot_id・タグやプロフィールの英語 enum 値)・処理の自己
# 言及をユーザー向け応答に出さない。主防御はプロンプト指示だが、層 1 の
# 安全網としてここで事後検査する(closed-world 検査と同じ位置づけ: 検出
# しても既にストリーミング済みのトークンは書き換えられないため、
# `state.degraded` へ記録するだけにとどめる)。
_FORBIDDEN_INTERNAL_TERMS: tuple[str, ...] = (
    # ask_user/profile のスロット名・タグの英語コード(フィールド名自体も
    # 含む。実測: 「移動手段の制限(mobility)は…」のような漏れが
    # 25_known_issues.md §3-1 で観測されている)。
    "mobility",
    "party",
    "pace",
    "interests",
    # Mobility/Party/Pace の enum 値。
    "avoid_walk",
    "short_walk_ok",
    "hike_ok",
    "family_kids",
    "packed",
    "relaxed",
    # 処理の自己言及(ツール名・ステップの実況)。2026-08-04 レビュー是正
    # (L-1): 「を実施しました」単独は「例大祭を実施しました」のような正当な
    # 文にも誤検知するため削除した(「検索を実施」等の複合語は残す)。
    "検索を実施",
    "ツールを実行",
    "処理を実行",
    "recommend を",
    "plan_itinerary",
    "edit_itinerary",
    "search_knowledge",
)
# 2026-08-04 レビュー是正(L-2): 元は `\bspot_[a-z0-9]+\b` だったが、Python の
# `re` は日本語文字も `\w`(Unicode 既定)として扱うため、「地点spot_017を」の
# ような日本語に直接続く spot_id では先頭の `\b` が成立せず検出漏れになって
# いた。ASCII 英数字・アンダースコア以外が前にあれば境界とみなす否定先読みに
# 変える(末尾は `[a-z0-9]+` が貪欲マッチするため追加の境界は不要)。
_FORBIDDEN_SPOT_ID_RE = re.compile(r"(?<![0-9A-Za-z_])spot_[a-z0-9]+", re.IGNORECASE)

# 2026-08-04 レビュー是正(M-2): `PreferenceKey`(interests のキー語彙。
# dialogue_style.md §3 論点 E が禁止する「タグやプロフィールの英語コード」)
# を禁止語に加える。日本語文中で誤検知しないよう(「water」等の英語圏の
# 固有名詞への部分一致を避けるため)、ASCII 英数字・アンダースコア以外が
# 前後にあることを要求する境界つき正規表現で検出する(spot_id と同じ理由で
# `\b` は使わない — 日本語直後に単語が続くケースを取りこぼすため)。
_PREFERENCE_ENUM_TERMS: tuple[str, ...] = tuple(key.value for key in PreferenceKey)
_FORBIDDEN_ENUM_RE = re.compile(
    r"(?<![0-9A-Za-z_])(?:"
    + "|".join(re.escape(term) for term in _PREFERENCE_ENUM_TERMS)
    + r")(?![0-9A-Za-z_])"
)


def find_forbidden_internal_terms(text: str) -> list[str]:
    """内部語・自己言及の検出(層 1 の安全網)。見つかった語をそのまま返す。"""

    found = [term for term in _FORBIDDEN_INTERNAL_TERMS if term in text]
    found.extend(dict.fromkeys(_FORBIDDEN_ENUM_RE.findall(text)))
    if _FORBIDDEN_SPOT_ID_RE.search(text):
        found.append("spot_id")
    return found


def ask_user_options_need_resolution_check(question: AskUserArgs) -> bool:
    """A7(§10 追加。dialogue_style.md 論点 C)の対象判定。

    対象は `kind=clarify` と `kind=preference` かつ `slot=origin` だけ。
    選好 enum の選択肢(interests/party/mobility/pace 等。値が nature や
    avoid_walk のような固定語彙)は、そもそも地点名ではなく解決不要の値
    なので対象外(dialogue_style.md §5 実装方針の注記どおり)。
    """

    if question.kind == "clarify":
        return True
    return question.kind == "preference" and question.slot is Slot.ORIGIN


def filter_unresolvable_ask_user_options(
    question: AskUserArgs,
    *,
    is_resolvable: Callable[[str], bool],
) -> tuple[AskUserArgs, list[str]]:
    """A7: 送出前に選択肢を名寄せし、解決できない選択肢だけを除去する。

    `is_resolvable` は呼び出し元(`tool_adapters.ToolAdapters.ask_user`)が
    `name_resolution.py` と同じ経路(`NameResolutionContext.resolve`)で
    組み立てる述語である。**質問ごとの差し戻しはしない**(ユーザー決定
    2026-08-04)。除去の結果、選択肢が 2 個未満になるかどうかの判定・
    recoverable な差し戻し(既存 A3 と同じ閾値)は呼び出し元が行う。

    対象外の `kind`/`slot` の組み合わせでは何もせず、除去件数 0 の
    `(question, [])` を返す。
    """

    if not ask_user_options_need_resolution_check(question):
        return question, []
    kept: list[AskUserOption] = []
    removed: list[str] = []
    for option in question.options:
        if is_resolvable(option.value):
            kept.append(option)
        else:
            removed.append(option.value)
    if not removed:
        return question, []
    return question.model_copy(update={"options": kept}), removed


def evaluate_ask_user(
    question: AskUserArgs,
    *,
    ask_user_count: int,
    asked_slots: Sequence[str],
    resolved_ambiguities: Sequence[object] = (),
    allowed_spot_ids: set[str] | None = None,
    existing_spot_ids: set[str] | None = None,
    profile: ProfileState | None = None,
) -> GuardResult:
    """`ask_user` の質の規律ガード(§10 R4・A1・A3〜A6)。

    メイン(`main_agent.py`)・レコメンド SA(`recommend_agent.py`)・知識検索 SA
    (`narration/search/agent.py` の ask コールバック経由)の 3 経路すべてが
    `ask_execution.execute_ask_user` を通じて本関数を呼ぶ。カウンタ
    (`ask_user_count`)はターン全体で合算する(R4)。旧 A2(質問を含む
    ターンの連続制限)は 2026-08-06 に廃止した(ADR-0024): 不明なものが
    残っていれば次のターンでも聞いてよい。

    `allowed_spot_ids`/`existing_spot_ids` を渡したときだけ A4(clarify の
    選択肢が実在 spot_id に解決できるか)を検査する。呼び出し元が spot_id
    以外の具体値(レコメンド SA の enum 値・知識検索の文書ラベル等)を使う
    場合は渡さなくてよい(A4 はメインエージェントの clarify 専用の防御)。

    `profile` を渡したときだけ A6(2026-08-04、レビュー是正・裁定17。最小
    実装)を検査する: `kind=preference` の該当 `slot` が既にプロフィールに
    値を持つなら「進められるのに念のため確認する」ことになるため聞かない。
    """

    if ask_user_count >= MAX_ASK_USER_PER_TURN:
        return GuardResult(
            False,
            "R4",
            f"このターンで質問できる回数の上限({MAX_ASK_USER_PER_TURN}回)に達しました",
        )
    if (
        question.kind == "preference"
        and question.slot is not None
        and question.slot.value in asked_slots
    ):
        return GuardResult(
            False,
            "A1",
            f"slot={question.slot.value} は質問済みです",
        )
    if (
        question.kind == "preference"
        and question.slot is not None
        and profile is not None
        and _preference_slot_already_known(profile, question.slot)
    ):
        return GuardResult(
            False,
            "A6",
            f"slot={question.slot.value} は既にプロフィールに値があります",
        )
    if not 2 <= len(question.options) <= 4:
        return GuardResult(False, "A3", "選択肢は 2〜4 個にしてください")
    if any(
        not option.label.strip() or not option.value.strip()
        for option in question.options
    ):
        return GuardResult(False, "A3", "空の選択肢は使えません")
    if question.kind == "clarify":
        if allowed_spot_ids is not None or existing_spot_ids is not None:
            allowed = allowed_spot_ids or set()
            existing = existing_spot_ids or set()
            for option in question.options:
                if option.value not in existing or option.value not in allowed:
                    return GuardResult(
                        False,
                        "A4",
                        f"選択肢が具体値に解決しません: {option.value}",
                    )
        normalized_surface = _normalize_surface(question.surface or "")
        if normalized_surface in {
            _normalize_surface(surface)
            for surface in _resolved_surfaces(resolved_ambiguities)
        }:
            return GuardResult(False, "A5", "同じ曖昧さは既に聞き返しています")
    return GuardResult(True)


def _preference_slot_already_known(profile: ProfileState, slot: Slot) -> bool:
    """A6 の最小実装: `slot` に対応する `ProfileState` の列が既に埋まっているか。

    `dates`/`origin`/`onboarding`(旅程固有の情報で、永続プロフィールには
    対応する列が無い)は常に `False`(=聞いてよい)を返す。
    """

    if slot is Slot.PARTY:
        return profile.party is not None
    if slot is Slot.MOBILITY:
        return profile.mobility is not None
    if slot is Slot.PACE:
        return profile.pace is not None
    if slot is Slot.INTERESTS:
        return bool(profile.interests)
    return False


def _resolved_surfaces(values: Sequence[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        if isinstance(value, str):
            result.append(value)
        elif isinstance(value, Mapping) and isinstance(value.get("surface"), str):
            result.append(value["surface"])
    return result


def _normalize_surface(value: str) -> str:
    return "".join(value.casefold().split())
