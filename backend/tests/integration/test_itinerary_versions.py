"""追記型の版、undo 分岐、制約 id 維持を compose DB で検証する。"""

import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from app.core.db import dispose_engine, session_scope
from app.db_models import Itinerary as ItineraryRow
from app.db_models import Thread, User
from app.domains.itinerary.repo import ItineraryRepository
from app.domains.itinerary.repo_types import ItineraryVersionConflictError
from app.domains.itinerary.types import Constraint, Itinerary, PredEnum

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="RUN_DB_INTEGRATION=1 のとき compose DB に対して実行する",
)


def _itinerary() -> Itinerary:
    return Itinerary(days=[], concessions=[], version=0)


async def test_append_only_revert_branch_and_constraint_ids() -> None:
    user_name = f"itinerary-version-{uuid4()}"
    constraint = Constraint(
        id="c_014",
        pred=PredEnum.NOT_CONSECUTIVE,
        args={"target": "神社"},
        weight=0.6,
        source_text="神社を続けない",
    )
    try:
        async with session_scope() as session:
            user = User(user_name=user_name, api_token=f"test-{uuid4()}")
            session.add(user)
            await session.flush()
            session.add(Thread(user_id=user.id))
            await session.flush()
            repository = ItineraryRepository(session)
            v1 = await repository.append_version(
                user_id=user.id,
                itinerary=_itinerary(),
                constraints=[constraint],
                origin="plan",
            )
            v2 = await repository.append_version(
                user_id=user.id,
                itinerary=_itinerary(),
                constraints=v1.constraints,
                origin="edit",
                expected_parent_version=1,
            )
            assert v2.parent_version == 1
            assert v2.constraints[0]["id"] == "c_014"

            with pytest.raises(ItineraryVersionConflictError):
                await repository.revert(user.id, expected_current_version=1)
            assert (await repository.get_current(user.id)).version == 2

            reverted = await repository.revert(
                user.id,
                expected_current_version=2,
            )
            assert reverted.version == 1
            assert len(await repository.list_versions(user.id)) == 2

            v3 = await repository.append_version(
                user_id=user.id,
                itinerary=_itinerary(),
                constraints=reverted.constraints,
                origin="edit",
                expected_parent_version=1,
            )
            versions = await repository.list_versions(user.id)
            assert [value.version for value in versions] == [1, 2, 3]
            assert v3.parent_version == 1
            assert all(value.constraints[0]["id"] == "c_014" for value in versions)
            assert sum(value.is_current for value in versions) == 1
            assert next(value.version for value in versions if value.is_current) == 3
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(ItineraryRow)
                    .where(ItineraryRow.user_id == user.id)
                )
                == 3
            )
    finally:
        async with session_scope() as session:
            user_id = await session.scalar(select(User.id).where(User.user_name == user_name))
            if user_id is not None:
                await session.execute(delete(User).where(User.id == user_id))
        await dispose_engine()
