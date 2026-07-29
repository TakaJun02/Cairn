import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.worker.app_db_models import User, Conversation, LanguageEnum, SpotRealtime

# 環境変数からデータベース接続情報を取得
DB_USER = os.getenv("APP_DB_USER", "app_user")
DB_PASSWORD = os.getenv("APP_DB_PASSWORD", "app_password")
DB_HOST = os.getenv("APP_DB_HOST", "app-db")
DB_PORT = os.getenv("APP_DB_PORT", "5432")
DB_NAME = os.getenv("APP_DB_NAME", "app_db")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(DATABASE_URL, client_encoding='utf8')
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db_session():
    """データベースセッションを返すジェネレータ"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- User 操作 ---

def get_or_create_user(db, user_name: str, language: str = 'ja'):
    """ユーザー名でユーザーを検索し、存在しない場合は新規作成する"""
    user = db.query(User).filter(User.user_name == user_name).first()
    if not user:
        print(f"User '{user_name}' not found. Creating new user.")
        try:
            lang_enum = LanguageEnum[language]
        except KeyError:
            print(f"Invalid language '{language}'. Defaulting to 'ja'.")
            lang_enum = LanguageEnum.ja
            
        user = User(user_name=user_name, language=lang_enum)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user

def update_user(db, user_id: int, profile: dict = None, itinerary: list = None):
    """ユーザーのプロファイルや周遊計画を更新する"""
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        if profile is not None:
            user.user_profile = profile
        if itinerary is not None:
            user.current_itinerary = itinerary
        db.commit()
        db.refresh(user)
    return user

# --- Conversation 操作 ---

def save_conversation(
    db,
    user_id: int,
    session_id: str,
    role: str,
    content: str,
    meta: dict = None
):
    """会話履歴をデータベースに保存する"""
    conversation = Conversation(
        user_id=user_id,
        session_id=session_id,
        role=role,
        content=content,
        meta=meta or {}
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation

def get_conversation_history(db, user_id: int, limit: int = 10):
    """指定したユーザーの最新の会話履歴を取得する"""
    return (
        db.query(Conversation)
        .filter(Conversation.user_id == user_id)
        .order_by(Conversation.timestamp.desc())
        .limit(limit)
        .all()
    )

def get_spot_realtime_map(db, spot_ids):
    """スポットIDのリストに対するリアルタイム情報を辞書で返す"""
    if not spot_ids:
        return {}
    records = (
        db.query(SpotRealtime)
        .filter(SpotRealtime.spot_id.in_(spot_ids))
        .all()
    )
    return {record.spot_id: record for record in records}
