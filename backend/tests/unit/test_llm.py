"""モデル固有の出力後処理を検証する。"""

from app.core.llm import normalize_generated_text


def test_normalize_generated_text_removes_think_blocks() -> None:
    value = "<think>内部推論\nをここへ書く</think>\n鳥海山をご案内します。"

    assert normalize_generated_text(value) == "鳥海山をご案内します。"
