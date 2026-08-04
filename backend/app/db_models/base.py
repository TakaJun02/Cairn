"""全 SQLAlchemy モデルが共有する metadata。"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Alembic と実行時コードで共有する宣言ベース。"""
