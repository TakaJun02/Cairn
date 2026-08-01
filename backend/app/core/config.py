"""環境変数をアプリケーション設定へ変換する唯一のモジュール。"""

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """`.env.example` と 1 対 1 に対応する実行時設定。"""

    model_config = SettingsConfigDict(
        env_file=_REPOSITORY_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    postgres_host: str = Field(default="db", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, ge=1, le=65535, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="guidance", min_length=1, alias="POSTGRES_DB")
    postgres_user: str = Field(default="guidance", min_length=1, alias="POSTGRES_USER")
    postgres_password: str = Field(default="", alias="POSTGRES_PASSWORD")

    osrm_car_url: str = Field(default="http://osrm-car:5000", alias="OSRM_CAR_URL")
    osrm_foot_url: str = Field(default="http://osrm-foot:5000", alias="OSRM_FOOT_URL")

    inference_server: str = Field(
        default="http://host.docker.internal:8000/v1", alias="INFERENCE_SERVER"
    )
    inference_model: str = Field(default="", alias="INFERENCE_MODEL")
    inference_timeout_sec: float = Field(default=120, gt=0, alias="INFERENCE_TIMEOUT_SEC")

    embedding_server: str = Field(default="", alias="EMBEDDING_SERVER")
    embedding_model: str = Field(default="Qwen/Qwen3-Embedding-8B", alias="EMBEDDING_MODEL")
    embedding_dim: int = Field(default=4096, gt=0, alias="EMBEDDING_DIM")

    tavily_api_key: str = Field(default="", alias="TAVILY_API_KEY")
    packs_root: Path = Field(default=Path("/packs"), alias="PACKS_ROOT")

    rt_mqtt_broker: str = Field(default="au1.cloud.thethings.network", alias="RT_MQTT_BROKER")
    rt_mqtt_port: int = Field(default=8883, ge=1, le=65535, alias="RT_MQTT_PORT")
    rt_mqtt_user: str = Field(default="", alias="RT_MQTT_USER")
    rt_mqtt_pass: str = Field(default="", alias="RT_MQTT_PASS")
    ttn_app_id: str = Field(default="", alias="TTN_APP_ID")
    ttn_device_id: str = Field(default="", alias="TTN_DEVICE_ID")

    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    cors_allow_origins: str = Field(default="http://localhost:5173", alias="CORS_ALLOW_ORIGINS")

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL は標準ログレベルで指定してください")
        return level

    @property
    def database_url(self) -> str:
        """asyncpg 用 URL。パスワードに記号があっても壊れない形にする。"""

        user = quote_plus(self.postgres_user)
        password = quote_plus(self.postgres_password)
        database = quote_plus(self.postgres_db)
        return (
            f"postgresql+asyncpg://{user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{database}"
        )

    @property
    def inference_max_attempts(self) -> int:
        """生成 API の最大試行回数。環境差を作らない固定値。"""

        return 3

    @property
    def inference_outer_timeout_sec(self) -> float:
        """全リトライを包む外側タイムアウトを内側設定から導出する。"""

        retry_backoff_sec = sum(2**attempt for attempt in range(self.inference_max_attempts - 1))
        return self.inference_timeout_sec * self.inference_max_attempts + retry_backoff_sec + 1

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """プロセス内で共有する不変の設定を返す。"""

    return Settings()
