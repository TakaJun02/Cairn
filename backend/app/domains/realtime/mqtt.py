"""TTN MQTT uplink を検証し、パック全スポットを 1 downlink で返す。"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import logging
import ssl
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.core.config import Settings
from app.core.db import session_scope
from app.domains.realtime.codec import decode_uplink, encode_downlink
from app.domains.realtime.scheduler import reserve_downlink

if TYPE_CHECKING:
    from app.db_models import PackJob

logger = logging.getLogger("app.realtime.mqtt")


def mqtt_is_configured(settings: Settings) -> bool:
    """未設定を例外ではなく明示的な正常縮退として判定する。"""

    return settings.realtime_mqtt_configured


class RealtimeMQTTService:
    """lifespan と同じ寿命で 1 接続だけを所有する。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> bool:
        if not mqtt_is_configured(self.settings):
            logger.info("mqtt_disabled_unconfigured", extra={"event": "mqtt_disabled_unconfigured"})
            return False
        if self._task is not None:
            return self.running
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="realtime-mqtt")
        return True

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        try:
            import aiomqtt
        except ImportError:
            logger.exception("mqtt_dependency_missing")
            return

        topic = self._uplink_topic()
        while not self._stop_event.is_set():
            try:
                async with aiomqtt.Client(
                    hostname=self.settings.rt_mqtt_broker,
                    port=self.settings.rt_mqtt_port,
                    username=self.settings.rt_mqtt_user,
                    password=self.settings.rt_mqtt_pass,
                    tls_context=ssl.create_default_context(),
                ) as client:
                    await client.subscribe(topic)
                    logger.info("mqtt_connected", extra={"topic": topic})
                    async for message in client.messages:
                        await self._handle_message(client, message.payload)
                        if self._stop_event.is_set():
                            break
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("mqtt_connection_failed")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=5.0)
                except TimeoutError:
                    pass

    async def _handle_message(self, client: Any, raw_message: Any) -> None:
        try:
            document = json.loads(bytes(raw_message))
            device_id = document["end_device_ids"]["device_id"]
            encoded = document["uplink_message"]["frm_payload"]
            if not isinstance(device_id, str) or not device_id:
                raise ValueError("device_id が不正です")
            if device_id != self.settings.ttn_device_id:
                logger.warning("uplink_device_not_configured", extra={"device_id": device_id})
                return
            if not isinstance(encoded, str):
                raise ValueError("frm_payload が文字列ではありません")
            payload = base64.b64decode(encoded, validate=True)
            uplink = decode_uplink(payload)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, binascii.Error):
            logger.exception("uplink_invalid")
            return
        if uplink is None:
            logger.warning(
                "uplink_unknown_version",
                extra={"device_id": device_id, "version": payload[0]},
            )
            return

        from app.db_models import PackJob, User

        async with session_scope(self.settings) as session:
            user_id = await session.scalar(
                select(User.id).where(User.lora_device_id == device_id)
            )
            if user_id is None:
                logger.warning("uplink_device_unknown", extra={"device_id": device_id})
                return
            job = await session.scalar(
                select(PackJob)
                .where(
                    PackJob.user_id == user_id,
                    PackJob.state.in_(["ready", "partial"]),
                )
                .order_by(PackJob.created_at.desc())
                .limit(1)
            )
            if job is None:
                logger.warning("uplink_pack_missing", extra={"device_id": device_id})
                return
            downlink = await self._build_downlink(session, job, uplink.pack_epoch)
            decision = await reserve_downlink(session, device_id)
            if not decision.allowed:
                return
            # publish の並行実行前に予約を確定し、同時 uplink でも上限を越えないようにする。
            await session.commit()

        await client.publish(
            self._downlink_topic(device_id),
            payload=json.dumps(
                {
                    "downlinks": [
                        {
                            "f_port": 2,
                            "frm_payload": base64.b64encode(downlink).decode("ascii"),
                            "priority": "NORMAL",
                        }
                    ]
                },
                separators=(",", ":"),
            ),
        )
        logger.info(
            "downlink_published",
            extra={"device_id": device_id, "bytes": len(downlink), "count": decision.count},
        )

    async def _build_downlink(
        self,
        session: Any,
        job: PackJob,
        requested_epoch: int,
    ) -> bytes:
        from app.domains.packs.storage import PackStorage
        from app.domains.realtime.store import RealtimeStore

        if requested_epoch != job.epoch:
            return encode_downlink(job.epoch, ())
        manifest = PackStorage(self.settings.packs_root).read_manifest(job.pack_id)
        if manifest is None:
            raise ValueError(f"manifest がありません: {job.pack_id}")
        raw_spots = manifest.get("spots")
        if not isinstance(raw_spots, list):
            raise ValueError("manifest.spots が配列ではありません")
        spot_ids: list[str] = []
        for item in raw_spots:
            if not isinstance(item, dict) or not isinstance(item.get("spot_id"), str):
                raise ValueError("manifest.spots の spot_id が不正です")
            spot_ids.append(item["spot_id"])
        states = await RealtimeStore(session).get_many(spot_ids)
        return encode_downlink(
            job.epoch,
            ((state.weather, state.congestion) for state in states),
        )

    def _uplink_topic(self) -> str:
        return (
            f"v3/{self.settings.ttn_app_id}@ttn/devices/"
            f"{self.settings.ttn_device_id}/up"
        )

    def _downlink_topic(self, device_id: str) -> str:
        return f"v3/{self.settings.ttn_app_id}@ttn/devices/{device_id}/down/push"
