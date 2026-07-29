from __future__ import annotations

from typing import List, Dict

LANG_HINT = {
    "ja": "日本語",
    "en": "English",
    "zh": "中文",
}

STYLE_HINT = {
        "ja": "落ち着いたガイド口調で、約45〜60秒（200〜300字程度）。固有名詞は正確に、過度な比喩は避ける。",
        "en": "Warm guide tone, about 45–60s (120–160 words). Precise proper nouns, minimal florid language.",
        "zh": "温和的导览口吻，约45–60秒（120–160词）。专有名词要准确，避免过度修辞。",
}

SAFETY_FOOTER = {
    "ja": "安全配慮: 天候・足元・野生動物への配慮を一言添えてください。過度な行動の推奨は避けます。",
    "en": "Safety: briefly remind about weather, footing, and wildlife awareness. Avoid encouraging risky behavior.",
    "zh": "安全提示：请简要提醒注意天气、路况与野生动物。避免鼓励冒险行为。",
}

PLAYBACK_CONTEXT_HINTS = {
    "ja": {
        "arrival": "旅行者はすでにスポットに到着し、目の前で説明を聞いています。未来の来訪を促す表現や「ご来訪の際は」のような語を避け、その場で見える・感じられる内容を現在形で描写してください。",
        "pass_by": "旅行者はルートを移動中にスポットのそばを通過しています。立ち寄りを前提とせず、今まさに車窓や足元から見えるポイントを現在形で紹介し、必要なら注意喚起を添えてください。",
        "default": "旅行者はいま、このスポットに関する音声案内を耳にしています。",
    },
    "en": {
        "arrival": "The traveler is already at the spot and listening on site. Avoid future-oriented invitations such as \"when you visit\"; speak in the present tense about what they can see or feel right now.",
        "pass_by": "The traveler is moving along the route, passing this spot without stopping. Describe in the present tense what they can notice in transit and avoid language that assumes a later visit.",
        "default": "The traveler is currently hearing guidance about this spot.",
    },
    "zh": {
        "arrival": "游客已经抵达景点并正在现场聆听，请用现在时描述此刻可以看到或感受到的内容，避免出现“到访时”等未来式表达。",
        "pass_by": "游客正沿路线经过该地点且不会停留。请用现在时说明路过时能看到的重点，并避免鼓励之后停下来参观的措辞。",
        "default": "游客正在聆听关于该景点的导览说明。",
    },
}

CONDITION_HINTS = {
    "ja": {
        "weather_1": "このスポットは曇り空です。どのスポットが曇りなのかわかるようにスポット名を含めてください。案内の最後に、天候の変化に注意するよう促す一文を自然な形で加えてください。",
        "weather_2": "このスポットは雨が降っています。どのスポットが雨なのかわかるようにスポット名を含めてください。案内の最後に、足元が滑りやすいことへの注意喚起を自然な形で加えてください。",
        "congestion_1": "このスポットは少し混雑しています。どのスポットが混雑しているのかわかるようにスポット名を含めてください。案内の最後に、周囲の人に配慮して楽しむよう促す短い一文を加えてください。",
        "congestion_2": "このスポットは大変混雑しています。どのスポットが混雑しているのかわかるようにスポット名を含めてください。案内の最後に、迷子や落とし物に注意し、譲り合って楽しむよう促す一文を加えてください。",
    },
    "en": {
        "weather_1": "This spot is cloudy. Please include the spot name so it's clear which spot is cloudy. At the end of the guide, naturally add a sentence urging people to be mindful of weather changes.",
        "weather_2": "It's raining at this location. Please include the location name so it's clear which spot is experiencing rain. At the end of the announcement, naturally add a warning about slippery footing.",
        "congestion_1": "This spot is a bit crowded. Please include the spot name so people know which one is busy. At the end of the guide, add a short sentence encouraging visitors to enjoy themselves while being considerate of others around them.",
        "congestion_2": "This spot is extremely crowded. Please include the spot name so visitors can identify which areas are busy. At the end of the notice, add a sentence reminding visitors to be mindful of lost children and lost items, and to enjoy the area by being considerate of others.",
    },
    "zh": {
        "weather_1": "该区域天气为多云。请包含区域名称以便明确区分多云区域。在导览结尾处，请自然地添加一句提醒注意天气变化的提示语。",
        "weather_2": "该区域正在下雨。请包含区域名称以便明确雨势位置。在指引结尾处，请自然地添加地面湿滑的注意事项。",
        "congestion_1": "该景点略显拥挤。请包含景点名称以便了解具体拥挤区域。在导览结尾处，请添加一句简短提示，提醒游客注意照顾周围人群，共同享受游览乐趣。",
        "congestion_2": "该景点人流密集。请注明具体景点名称以便游客知晓拥挤区域。在导览结尾处，请添加一句提示语，提醒游客注意防止走失和遗失物品，并倡导互相礼让、愉快游玩。",
    },
}

# 通常のガイダンスプロンプトのテンプレート
GUIDANCE_PROMPT_TEMPLATES = {
    "ja": """
[LANGUAGE={lang}|{lang_label}] [STYLE={style}]
あなたは鳥海山エリアを訪れる観光客向けのプロのツアーガイドです。

スポット名: {name}
参考情報:
{facts_txt}

上記の参考情報に基づき、{lang_label}で話すためのナレーション原稿を作成してください。
制約:
- シーン: {context_note}
- {style_note}
- 構成: 1)短い導入、2)主要な事実・歴史・自然、3)豆知識、4)安全への言及。
- 不確かな情報は避け、不明な点は簡潔にそう述べる。
- 「この資料では」や「文脈によると」といった表現は使わないでください。
- 出力はプレーンテキストのみとします。

{safety_footer}
""".strip(),
    "en": """
[LANGUAGE={lang}|{lang_label}] [STYLE={style}]
You are a professional tour guide for tourists visiting the Mount Chokai area.

Spot Name: {name}
Reference Information:
{facts_txt}

Based on the reference information above, please create a narration script to be spoken in {lang_label}.
Constraints:
- Context: {context_note}
- {style_note}
- Structure: 1) A brief introduction, 2) Key facts, history, or nature, 3) A piece of trivia, 4) A mention of safety.
- Avoid uncertain information. If something is unknown, state it concisely.
- Do not use phrases like "According to this document" or "Based on the context."
- The output must be plain text only.

{safety_footer}
""".strip(),
    "zh": """
[LANGUAGE={lang}|{lang_label}] [STYLE={style}]
你是一位为游览鸟海山地区的游客服务的专业导游。

景点名称: {name}
参考信息:
{facts_txt}

请根据以上参考信息，创建一段用于{lang_label}播讲的解说稿。
限制:
- 场景: {context_note}
- {style_note}
- 结构：1) 简短介绍，2) 主要事实、历史、自然，3) 小知识，4) 安全提示。
- 避免不确定的信息，如遇未知情况请简洁说明。
- 请不要使用“根据这份资料”或“从上下文中看”之类的表述。
- 仅输出纯文本。

{safety_footer}
""".strip()
}

# 状況別案内のプロンプトテンプレート
SITUATIONAL_PROMPT_TEMPLATES = {
    "ja": """
[TASK=Generate a situational guidance message] [LANGUAGE={lang}|{lang_label}]
あなたは鳥海山エリアを訪れる観光客向けのプロのツアーガイドです。

スポット「{name}」の現在の状況を伝える、簡潔な音声案内を作成してください。

スポット名: {name}
現在の状況: {condition_instruction}

指示:
- 必ずスポット名から始め、「{name}は現在、少し混雑しています。」のように、現在の状況を伝える文章を作成してください。
- 非常に簡潔（1〜2文程度）な、{lang_label}の話し言葉で記述してください。
- 役立つ、分かりやすい口調を心がけてください。
- スポットの歴史や自然に関する追加情報は含めず、与えられた状況に関する案内に徹してください。
- 出力はプレーンテキストのみとします。
""".strip(),
    "en": """
[TASK=Generate a situational guidance message] [LANGUAGE={lang}|{lang_label}]
You are a professional tour guide for tourists visiting the Mount Chokai area.

Please create a concise audio guidance message that communicates the current situation at the spot "{name}".

Spot Name: {name}
Current Situation: {condition_instruction}

Instructions:
- Always start with the spot name and create a sentence that describes the current situation, like "{name} is currently a bit crowded."
- Write in a very concise (about 1-2 sentences), conversational {lang_label}.
- Please use a helpful and easy-to-understand tone.
- Do not include additional information about the spot's history or nature; focus strictly on the guidance related to the given situation.
- The output must be plain text only.
""".strip(),
    "zh": """
[TASK=Generate a situational guidance message] [LANGUAGE={lang}|{lang_label}]
你是一位为游览鸟海山地区的游客服务的专业导游。

请针对景点“{name}”的当前状况，生成一段简洁的语音导览信息。

景点名称: {name}
当前状况: {condition_instruction}

指示:
- 请务必以景点名称开头，并像“{name}目前有些拥挤。”这样，创建一个描述当前状况的句子。
- 请使用非常简洁（大约1-2句话）的口语化{lang_label}进行描述。
- 请注意使用有帮助且易于理解的语气。
- 不要包含景点的历史或自然等额外信息，请专注于提供与当前状况相关的引导。
- 仅输出纯文本。
""".strip()
}

def _join_context(ctx: List[Dict], max_chars: int = 8000) -> str:
    # 単純に text を連結（必要なら将来トークン制御）
    texts = []
    total = 0
    for c in ctx or []:
        t = (c.get("text") or "").strip()
        if not t:
            continue
        total += len(t)
        texts.append(t)
        if total >= max_chars:
            break
    return "\n\n".join(texts)


def build_prompt(spot: Dict, ctx: List[Dict], lang: str, style: str = "narration") -> str:
    """
    音声ナレーション向けのプロンプトを構築。
    spot辞書に situation が含まれるかで、生成するプロンプトを切り替える。
    - situation 無し: 通常のスポット説明（RAG利用）
    - situation 有り: 天候・混雑度に応じた状況別案内
    """
    lang_label = LANG_HINT.get(lang, lang)
    name = spot.get("name") or spot.get("spot_id", "this spot")
    spot_id = spot.get("spot_id")
    situation = spot.get("situation")
    playback = spot.get("playback") or "arrival"

    context_note = PLAYBACK_CONTEXT_HINTS.get(lang, {}).get(playback)
    if not context_note:
        context_note = PLAYBACK_CONTEXT_HINTS.get(lang, {}).get("default")
    if not context_note:
        context_note = PLAYBACK_CONTEXT_HINTS.get("en", {}).get(playback, "")

    # --- situation がある場合：状況説明のプロンプトを生成 ---
    if situation:
        condition_instruction = CONDITION_HINTS.get(lang, {}).get(situation, "")
        template = SITUATIONAL_PROMPT_TEMPLATES.get(lang, SITUATIONAL_PROMPT_TEMPLATES["en"]) # デフォルトは英語

        prompt = template.format(
            lang=lang,
            lang_label=lang_label,
            name=name,
            spot_id=spot_id,
            condition_instruction=condition_instruction
        )
        return prompt

    # --- situation がない場合：スポットのガイダンステキストのプロンプトを生成 ---
    else:
        desc = (spot.get("description") or "").strip()
        context_block = _join_context(ctx, max_chars=8000)
        facts_lines = []
        if desc:
            facts_lines.append(f"- POI.description: {desc}")
        if context_block:
            facts_lines.append(f"- RAG:\n{context_block}")
        facts_txt = "\n".join(facts_lines) if facts_lines else "- (no extra context)"

        style_note = STYLE_HINT.get(lang, "guidance")
        safety_footer = SAFETY_FOOTER.get(lang, "")
        template = GUIDANCE_PROMPT_TEMPLATES.get(lang, GUIDANCE_PROMPT_TEMPLATES["en"]) # デフォルトは英語

        prompt = template.format(
            lang=lang,
            lang_label=lang_label,
            name=name,
            spot_id=spot_id,
            facts_txt=facts_txt,
            context_note=context_note or "",
            style=style,
            style_note=style_note,
            safety_footer=safety_footer
        )
        return prompt
