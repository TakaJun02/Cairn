"""`/packs` の原子的な書き込み、静的配信、手動 GC。"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.db_models import PackAsset, PackJob

IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
DEFAULT_GC_KEEP = 3


class PackStorageError(RuntimeError):
    """パックの安全な書き込み・公開に失敗した。"""


class ImmutablePackStaticFiles(StaticFiles):
    """作業ディレクトリを隠し、公開済み成果物へ長期キャッシュを付ける。"""

    async def get_response(self, path: str, scope: dict[str, Any]) -> Response:
        if any(part.startswith(".") for part in PurePosixPath(path).parts):
            return Response(status_code=404)
        response = await super().get_response(path, scope)
        if response.status_code in {200, 206}:
            response.headers["Cache-Control"] = IMMUTABLE_CACHE_CONTROL
        return response


class PackStorage:
    """生成中は `.work` に隔離し、manifest 完成時だけディレクトリを公開する。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.work_root = self.root / ".work"

    def prepare(self, pack_id: UUID | str) -> Path:
        key = _pack_key(pack_id)
        self.root.mkdir(parents=True, exist_ok=True)
        self.work_root.mkdir(parents=True, exist_ok=True)
        work = self.work_root / key
        published = self.root / key
        if work.exists():
            return work
        if published.exists():
            os.replace(published, work)
        else:
            work.mkdir(parents=True)
        (work / "audio").mkdir(parents=True, exist_ok=True)
        return work

    def write_route(self, pack_id: UUID | str, route_geojson: dict[str, Any]) -> Path:
        return self._write_json(self.prepare(pack_id) / "route.geojson", route_geojson)

    def write_manifest(self, pack_id: UUID | str, manifest: dict[str, Any]) -> Path:
        return self._write_json(self.prepare(pack_id) / "manifest.json", manifest)

    def write_audio(
        self,
        pack_id: UUID | str,
        spot_id: str,
        variant: str,
        value: bytes,
    ) -> Path:
        if not value:
            raise PackStorageError("空の音声は書き込めません")
        if not spot_id or "/" in spot_id or "\\" in spot_id or ".." in spot_id:
            raise PackStorageError(f"安全でない spot_id です: {spot_id!r}")
        if not variant or "/" in variant or "\\" in variant or ".." in variant:
            raise PackStorageError(f"安全でない variant です: {variant!r}")
        path = self.prepare(pack_id) / "audio" / f"{spot_id}.{variant}.ja.mp3"
        _atomic_write(path, value)
        return path

    def audio_exists(self, pack_id: UUID | str, spot_id: str, variant: str) -> bool:
        key = _pack_key(pack_id)
        name = f"{spot_id}.{variant}.ja.mp3"
        return (self.work_root / key / "audio" / name).is_file() or (
            self.root / key / "audio" / name
        ).is_file()

    def publish(self, pack_id: UUID | str) -> Path:
        key = _pack_key(pack_id)
        work = self.work_root / key
        published = self.root / key
        if not (work / "manifest.json").is_file() or not (work / "route.geojson").is_file():
            raise PackStorageError("manifest.json と route.geojson が揃っていません")
        if published.exists():
            raise PackStorageError(f"公開先が既に存在します: {published}")
        os.replace(work, published)
        return published

    def read_manifest(self, pack_id: UUID | str) -> dict[str, Any] | None:
        path = self.root / _pack_key(pack_id) / "manifest.json"
        if not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None

    def delete_pack(self, pack_id: UUID | str) -> None:
        key = _pack_key(pack_id)
        for path in (self.root / key, self.work_root / key):
            if path.is_dir():
                shutil.rmtree(path)

    def pack_directories(self) -> set[str]:
        if not self.root.is_dir():
            return set()
        return {
            path.name
            for path in self.root.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        }

    def work_directories(self) -> set[str]:
        if not self.work_root.is_dir():
            return set()
        return {path.name for path in self.work_root.iterdir() if path.is_dir()}

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> Path:
        encoded = (
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        _atomic_write(path, encoded)
        return path


async def garbage_collect_packs(
    session: AsyncSession,
    storage: PackStorage,
    *,
    keep: int = DEFAULT_GC_KEEP,
    dry_run: bool = False,
) -> dict[str, Any]:
    """ユーザーごとに新しい `keep` 件を残し、孤児は削除せず報告する。"""

    if keep < 0:
        raise ValueError("keep は 0 以上にしてください")
    rows = (
        await session.scalars(
            select(PackJob).order_by(
                PackJob.user_id,
                PackJob.created_at.desc(),
                PackJob.id.desc(),
            )
        )
    ).all()
    by_user: dict[int, list[PackJob]] = defaultdict(list)
    for row in rows:
        by_user[row.user_id].append(row)

    removable: list[PackJob] = []
    for user_rows in by_user.values():
        protected_ids = {row.id for row in user_rows[:keep]}
        removable.extend(
            row
            for row in user_rows
            if row.id not in protected_ids and row.state in {"ready", "partial", "failed"}
        )
    db_pack_ids = {str(row.pack_id) for row in rows}
    orphan_directories = sorted(storage.pack_directories() - db_pack_ids)
    orphan_work_directories = sorted(storage.work_directories() - db_pack_ids)
    deleted_pack_ids = [str(row.pack_id) for row in removable]

    if not dry_run and removable:
        pack_ids = [row.pack_id for row in removable]
        job_ids = [row.id for row in removable]
        await session.execute(delete(PackAsset).where(PackAsset.pack_id.in_(pack_ids)))
        await session.execute(delete(PackJob).where(PackJob.id.in_(job_ids)))
        await session.flush()
        for pack_id in pack_ids:
            storage.delete_pack(pack_id)

    return {
        "keep": keep,
        "dry_run": dry_run,
        "deleted_pack_ids": deleted_pack_ids,
        "orphan_directories": orphan_directories,
        "orphan_work_directories": orphan_work_directories,
    }


def _pack_key(pack_id: UUID | str) -> str:
    try:
        return str(UUID(str(pack_id)))
    except ValueError as exc:
        raise PackStorageError(f"pack_id が UUID ではありません: {pack_id!r}") from exc


def _atomic_write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".tmp-", delete=False) as output:
            temporary = output.name
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
        raise PackStorageError(f"ファイルを書き込めませんでした: {path}") from exc
