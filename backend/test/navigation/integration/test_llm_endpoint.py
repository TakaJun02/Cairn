def test_llm_endpoint__returns_texts_for_spots(monkeypatch, client_llm):
    from backend.worker.app.services.llm import generator, prompt

    # retrieve と generate をスタブ
    monkeypatch.setattr(generator, "retrieve_context",
                        lambda spot_ref, lang: [{"text": f"context for {spot_ref.get('spot_id')} in {lang}"}],
                        raising=True)
    monkeypatch.setattr(generator, "generate_text",
                        lambda p: f"GENERATED: {p[:30]}...",
                        raising=True)

    # prompt の体裁が混ざることを軽く検査
    def fake_build_prompt(spot, ctx, lang, style="narration"):
        return f"[{lang}|{style}] {spot.get('name','')}::{ctx[0]['text']}"

    monkeypatch.setattr(prompt, "build_prompt", fake_build_prompt, raising=True)

    body = {
        "language": "ja",
        "jobs": [
            {"job_id": "job-1",
             "spot": {"spot_id": "A", "name": "丸池様", "description": "透明度の高い湧水池。", "md_slug": "spot_maruike_sama"}},
            {"job_id": "job-2",
             "spot": {"spot_id": "B", "name": "法体の滝", "description": "落差57mの名瀑。", "md_slug": "spot_hottai_falls"}},
        ],
    }
    res = client_llm.post("/describe", json=body)
    assert res.status_code == 200
    data = res.json()

    assert "items" in data and len(data["items"]) == 2
    assert data["items"][0]["job_id"] == "job-1"
    assert data["items"][0]["spot_id"] == "A"
    assert data["items"][1]["spot_id"] == "B"
    assert data["items"][0]["text"].startswith("GENERATED:")
