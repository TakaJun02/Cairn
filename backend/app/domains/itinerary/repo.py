"""旅程スナップショットの追記と `is_current` ポインタ移動。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db_models import Itinerary as ItineraryRow
from app.db_models import Spot, Thread, TravelTime, User
from app.domains.itinerary.repo_types import (
    ItineraryNotFoundError,
    ItineraryRepositoryError,
    ItineraryVersion,
    ItineraryVersionConflictError,
    PlanningData,
)
from app.domains.itinerary.solver import PlanningSpot, TravelTimeMatrix
from app.domains.itinerary.types import Constraint, Itinerary

__all__ = ["ItineraryRepository", "ItineraryRepositoryError"]


class ItineraryRepository:
    """呼び出し側のトランザクションに参加し、暗黙 commit はしない。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def commit(self) -> None:
        """API 境界など、呼び出し側が選んだ時点で変更を確定する。"""

        await self.session.commit()

    async def get_current(
        self, user_id: int, *, for_update: bool = False
    ) -> ItineraryVersion | None:
        statement = select(ItineraryRow).where(
            ItineraryRow.user_id == user_id,
            ItineraryRow.is_current.is_(True),
        )
        if for_update:
            statement = statement.with_for_update()
        row = await self.session.scalar(statement)
        return _version_from_row(row) if row is not None else None

    async def get_version(self, user_id: int, version: int) -> ItineraryVersion | None:
        row = await self.session.scalar(
            select(ItineraryRow).where(
                ItineraryRow.user_id == user_id,
                ItineraryRow.version == version,
            )
        )
        return _version_from_row(row) if row is not None else None

    async def list_versions(self, user_id: int) -> list[ItineraryVersion]:
        rows = (
            await self.session.scalars(
                select(ItineraryRow)
                .where(ItineraryRow.user_id == user_id)
                .order_by(ItineraryRow.version)
            )
        ).all()
        return [_version_from_row(row) for row in rows]

    async def append_version(
        self,
        *,
        user_id: int,
        itinerary: Itinerary,
        constraints: list[Constraint | dict[str, Any]],
        origin: str,
        created_by_message_id: int | None = None,
        expected_parent_version: int | None = None,
    ) -> ItineraryVersion:
        """ユーザー行で直列化し、旧本文を更新せず次版を追記する。"""

        if origin not in {"plan", "edit", "revert"}:
            raise ValueError(f"未定義の origin です: {origin}")
        await self._lock_user(user_id)
        current = await self.get_current(user_id, for_update=True)
        parent_version = current.version if current is not None else None
        if expected_parent_version is not None and parent_version != expected_parent_version:
            raise ItineraryVersionConflictError(
                f"現在版が v{expected_parent_version} から v{parent_version} に変わりました"
            )
        if current is None and origin != "plan":
            raise ItineraryNotFoundError("編集対象の旅程がありません")
        if current is not None and origin == "plan":
            raise ItineraryVersionConflictError("初回 plan の旅程が既に存在します")
        next_version = int(
            await self.session.scalar(
                select(func.coalesce(func.max(ItineraryRow.version), 0) + 1).where(
                    ItineraryRow.user_id == user_id
                )
            )
            or 1
        )
        if current is not None:
            await self.session.execute(
                update(ItineraryRow)
                .where(
                    ItineraryRow.user_id == user_id,
                    ItineraryRow.version == current.version,
                )
                .values(is_current=False)
            )
            await self.session.flush()
        persisted = itinerary.model_copy(update={"version": next_version}, deep=True)
        row = ItineraryRow(
            user_id=user_id,
            version=next_version,
            parent_version=parent_version,
            is_current=True,
            body=_body_json(persisted),
            constraints=[_constraint_json(value) for value in constraints],
            origin=origin,
            created_by_message_id=created_by_message_id,
        )
        self.session.add(row)
        await self.session.flush()
        return _version_from_row(row)

    async def revert(
        self,
        user_id: int,
        *,
        to_version: int | None = None,
        expected_current_version: int | None = None,
    ) -> ItineraryVersion:
        """行を作らず、現在位置だけを親版または指定版へ移す。"""

        await self._lock_user(user_id)
        current = await self.get_current(user_id, for_update=True)
        if current is None:
            raise ItineraryNotFoundError("戻す旅程がありません")
        _check_expected_current(current, expected_current_version)
        target_version = to_version if to_version is not None else current.parent_version
        if target_version is None:
            raise ItineraryNotFoundError("これ以上前の旅程版はありません")
        return await self._move_current(user_id, current.version, target_version)

    async def redo(
        self,
        user_id: int,
        *,
        to_version: int | None = None,
        expected_current_version: int | None = None,
    ) -> ItineraryVersion:
        """行を作らず、現在版を親とする既存子版へ現在位置を進める。"""

        await self._lock_user(user_id)
        current = await self.get_current(user_id, for_update=True)
        if current is None:
            raise ItineraryNotFoundError("進める旅程がありません")
        _check_expected_current(current, expected_current_version)
        if to_version is None:
            children = (
                await self.session.scalars(
                    select(ItineraryRow.version)
                    .where(
                        ItineraryRow.user_id == user_id,
                        ItineraryRow.parent_version == current.version,
                    )
                    .order_by(ItineraryRow.version)
                )
            ).all()
            if not children:
                raise ItineraryNotFoundError("やり直せる旅程版がありません")
            if len(children) > 1:
                raise ItineraryVersionConflictError(
                    "分岐した子版が複数あります。to_version を指定してください"
                )
            to_version = int(children[0])
        target = await self.get_version(user_id, to_version)
        if target is None or target.parent_version != current.version:
            raise ItineraryNotFoundError(f"現在版から進めない版です: v{to_version}")
        return await self._move_current(user_id, current.version, to_version)

    async def get_pending_constraints(
        self, user_id: int, *, for_update: bool = False
    ) -> list[dict[str, Any]]:
        """`threads.pending_constraints` を読む。

        `for_update` は受け取るが**常に無視する**(2026-08-04、レビュー是正:
        Critical — `plan_itinerary` 実行中に `threads` 行を `SELECT ... FOR
        UPDATE` すると、同じターンが後で `ask_user` を実行したとき
        `write_pending_ask_now` が別セッションで同じ行を UPDATE しようとして
        ロック待ちになり、待っているのはターン自身のコルーチンなので自己
        デッドロックになる。同時実行は `ActiveTurnRegistry` の 409 が既に
        防いでいるため、ターン処理中に `threads` 行ロックを保持する必要は
        ない)。引数は既存呼び出し元との互換のため残す。
        """

        del for_update
        thread = await self.session.scalar(select(Thread).where(Thread.user_id == user_id))
        if thread is None:
            return []
        return deepcopy(list(thread.pending_constraints))

    async def clear_pending_constraints(self, user_id: int) -> None:
        await self.session.execute(
            update(Thread).where(Thread.user_id == user_id).values(pending_constraints=[])
        )

    async def load_spot_names(self) -> dict[str, str]:
        """spot_id → name_ja だけを引く軽量版(2026-08-04、[25 §1-7])。

        `load_planning_data` は spots 全カラム + travel_times 全件を読む
        ため、譲歩文の名前解決だけが目的の呼び出し元(undo/redo・GET の
        送出層)には過剰である。ここでは `Spot.spot_id`/`Spot.name_ja` の
        2 列だけを選択する。
        """

        rows = (
            await self.session.execute(select(Spot.spot_id, Spot.name_ja))
        ).all()
        return {row.spot_id: row.name_ja for row in rows}

    async def load_planning_data(self) -> PlanningData:
        spot_rows = (
            await self.session.execute(
                select(
                    Spot.spot_id,
                    Spot.tags_ja,
                    Spot.stay_min,
                    Spot.open_hours,
                    Spot.season_closed_months,
                    Spot.kind,
                    Spot.name_ja,
                ).order_by(Spot.spot_id)
            )
        ).all()
        travel_rows = (
            await self.session.execute(
                select(
                    TravelTime.from_spot_id,
                    TravelTime.to_spot_id,
                    TravelTime.mode,
                    TravelTime.duration_sec,
                )
            )
        ).all()
        spots = {
            row.spot_id: PlanningSpot(
                spot_id=row.spot_id,
                tags_ja=tuple(row.tags_ja),
                stay_min=int(row.stay_min),
                open_hours=row.open_hours,
                season_closed_months=tuple(row.season_closed_months),
                kind=row.kind,
                name_ja=row.name_ja,
            )
            for row in spot_rows
        }
        matrix = TravelTimeMatrix(
            {
                (row.from_spot_id, row.to_spot_id, row.mode): int(row.duration_sec)
                for row in travel_rows
            }
        )
        return PlanningData(spots=spots, travel_times=matrix)

    async def _move_current(
        self, user_id: int, current_version: int, target_version: int
    ) -> ItineraryVersion:
        target_row = await self.session.scalar(
            select(ItineraryRow)
            .where(
                ItineraryRow.user_id == user_id,
                ItineraryRow.version == target_version,
            )
            .with_for_update()
        )
        if target_row is None:
            raise ItineraryNotFoundError(f"旅程版が見つかりません: v{target_version}")
        await self.session.execute(
            update(ItineraryRow)
            .where(
                ItineraryRow.user_id == user_id,
                ItineraryRow.version == current_version,
            )
            .values(is_current=False)
        )
        await self.session.flush()
        await self.session.execute(
            update(ItineraryRow)
            .where(
                ItineraryRow.user_id == user_id,
                ItineraryRow.version == target_version,
            )
            .values(is_current=True)
        )
        await self.session.flush()
        target_row.is_current = True
        return _version_from_row(target_row)

    async def _lock_user(self, user_id: int) -> None:
        found = await self.session.scalar(
            select(User.id).where(User.id == user_id).with_for_update()
        )
        if found is None:
            raise ItineraryNotFoundError(f"ユーザーが見つかりません: {user_id}")


def _version_from_row(row: ItineraryRow) -> ItineraryVersion:
    body = deepcopy(dict(row.body))
    body["version"] = row.version
    return ItineraryVersion(
        user_id=row.user_id,
        version=row.version,
        parent_version=row.parent_version,
        is_current=row.is_current,
        itinerary=Itinerary.model_validate(body),
        constraints=deepcopy(list(row.constraints)),
        origin=row.origin,
        created_by_message_id=row.created_by_message_id,
    )


def _body_json(itinerary: Itinerary) -> dict[str, Any]:
    return itinerary.model_dump(mode="json", exclude={"version"}, by_alias=True)


def _constraint_json(value: Constraint | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, Constraint):
        return value.model_dump(mode="json")
    return deepcopy(value)


def _check_expected_current(
    current: ItineraryVersion,
    expected_current_version: int | None,
) -> None:
    if (
        expected_current_version is not None
        and current.version != expected_current_version
    ):
        raise ItineraryVersionConflictError(
            (
                f"現在版は v{expected_current_version} ではなく "
                f"v{current.version} です"
            ),
            current_version=current.version,
        )
