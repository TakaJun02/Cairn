"""資格情報未設定時は MQTT タスクを作らない。"""

import logging

from app.core.config import Settings
from app.domains.realtime.mqtt import RealtimeMQTTService, mqtt_is_configured


async def test_unconfigured_mqtt_is_a_normal_noop(caplog) -> None:
    caplog.set_level(logging.INFO)
    settings = Settings(
        _env_file=None,
        POSTGRES_PASSWORD="test",
        RT_MQTT_USER="",
        RT_MQTT_PASS="",
        TTN_APP_ID="",
        TTN_DEVICE_ID="",
    )
    service = RealtimeMQTTService(settings)

    assert mqtt_is_configured(settings) is False
    assert await service.start() is False
    assert service.running is False
    assert "mqtt_disabled_unconfigured" in caplog.text
    await service.stop()


def test_mqtt_requires_explicit_opt_in_and_complete_settings() -> None:
    values = {
        "_env_file": None,
        "POSTGRES_PASSWORD": "test",
        "RT_MQTT_USER": "app@ttn",
        "RT_MQTT_PASS": "secret",
        "TTN_APP_ID": "app",
        "TTN_DEVICE_ID": "device",
    }

    assert mqtt_is_configured(Settings(**values)) is False
    assert mqtt_is_configured(Settings(**values, RT_MQTT_ENABLED=True)) is True
