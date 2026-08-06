"""固定プレフィックスを保つ update_profile/main_agent/respond プロンプト。

`Docs/30_design/agent_react_architecture.md` §3.1 の順序(①システムプロンプト+
Tool 定義+出力スキーマ → ②プロフィール → ③現在の旅程+有効な制約 →
④会話履歴 → ⑤このターンの軌跡 → ⑥ユーザーの発話)を、
`build_main_agent_messages`/`build_respond_messages` がこの順で組み立てる。

① (システムメッセージ)はターン間で byte 同一に保つ(可変情報を混ぜない。
prefix caching のため)。R1/R2 のガードレールが発動したときだけ、例外的に
縮小スキーマ(`main_agent_done_only_schema`)へ切り替える(これは
`Docs/30_design/agent_react_architecture.md` §10 が明示的に許した縮退である)。

`build_respond_messages` は `Docs/30_design/dialogue_style.md` §4 の入力構成
(① 軌跡 / ② 譲歩・縮退 / ③ 会話履歴 / ④ ユーザーの発話 / **⑤ 素材**)に従う。
⑤ 素材(このターンで提示したスポットの説明)は `respond.py` が
`static.spots` から読み取り専用クエリで取得し、`SpotMaterial` として渡す
(論点 A2: ツールのダイジェスト/メインループには足さない。respond の入力に
だけ足す)。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.domains.conversation.itinerary_digest import (
    format_active_constraints,
    format_itinerary_digest,
)
from app.domains.conversation.state import TurnState
from app.domains.conversation.types import (
    ResponseMode,
    TrajectoryStep,
    pred_values,
    preference_values,
    slot_values,
)
from app.domains.recommendation.types import Mobility

_PREFERENCE_VOCABULARY = " | ".join(preference_values())
_MOBILITY_VOCABULARY = " | ".join(f'"{value.value}"' for value in Mobility)
_PRED_VOCABULARY = " | ".join(pred_values())
_SLOT_VOCABULARY = " | ".join(slot_values())
_JAPAN_TZ = ZoneInfo("Asia/Tokyo")
_WEEKDAYS_JA = ("月", "火", "水", "木", "金", "土", "日")
# dialogue_style.md §4: ⑤ 素材の上限件数(候補 + 旅程 diff 追加分の合計)と、
# 素材 1 件あたりの description 表示上限文字数(暴走防止。rerank.py の
# description_ja_db 切り詰め[:360]と同じ発想で、respond 入力用にやや短く保つ)。
RESPOND_MATERIALS_MAX_SPOTS = 8
RESPOND_MATERIAL_DESCRIPTION_MAX_CHARS = 240


@dataclass(frozen=True, slots=True)
class SpotMaterial:
    """⑤ 素材 1 件分(dialogue_style.md §4)。`static.spots` から読み取る。

    `description`/`social_proof` は当該言語(現状 `users.language` は未実装
    — 20_architecture.md §10 — のため常に日本語)、無ければ `None`。
    """

    description: str | None = None
    social_proof: str | None = None
    tags_ja: tuple[str, ...] = ()

MAIN_AGENT_MAX_DAYS = 5
MAIN_AGENT_MAX_MUST_VISIT = 8
# recommend 1 回分の結果件数(コード側で k=3 に固定。2026-08-04、
# dialogue_style.md §4 決定: 推薦は 3 件に固定し、respond が 3 件すべてを
# 語る)を転記できる余地を持たせつつ、複数回の recommend 結果を積み増しても
# 暴走しない上限(ADR-0022)。recommend 自体に候補数の上限引数はない
# (L-2 是正: 旧コメントは「上限 8」と書いていたが、そのような口は存在しない)。
MAIN_AGENT_MAX_CANDIDATE_SPOTS = 12
MAIN_AGENT_MAX_OPS = 8
MAIN_AGENT_MAX_CONSTRAINTS_ADD = 8
MAIN_AGENT_MAX_CONSTRAINTS_REMOVE = 8
MAIN_AGENT_MAX_ASSUMPTIONS = 6


UPDATE_PROFILE_SYSTEM_PROMPT = f"""あなたは鳥海山観光ガイダンスの
update_profile ステップです。
会話履歴と最新のユーザー発話から、ユーザーの恒久的な選好の差分
(profile_delta)と、このターン限りの点数調整(score_adjustments)だけを、
指定された JSON Schema の JSON 1 個へ書き出してください。
説明文や Markdown は出しません。

出力フィールドは profile_delta → score_adjustments の順で埋めます。

境界:
- profile_delta は「今回新たに分かった、または変わった」ことだけを書きます。
  現在のプロフィールに既にある値を繰り返し書きません。
  何も変わらなければ profile_delta は null にします。
- profile_delta.interests のキーは次の 12 語だけです: {_PREFERENCE_VOCABULARY}
- party / mobility / pace は恒久的な設定として確定した場合だけ書きます。
  mobility は移動手段ではなく歩行耐性です。値は {_MOBILITY_VOCABULARY}
  だけです。「車で行く」「車で回る」だけでは歩行耐性は不明なので書きません。
- score_adjustments はそのターン限りの注文(「静かな所がいい」等)です。
  恒久的な選好なら profile_delta.interests に書き、score_adjustments には
  書きません。両方に書くと二重に効きます。
- score_adjustments.spot_id は②の参照可能な spot_id 語彙にある地点だけです。
  語彙に無い地点は書きません。
- 更新することが何もなければ profile_delta=null、score_adjustments=[] を
  返します。空でも構いません。

選好キー語彙(固定): {_PREFERENCE_VOCABULARY}
"""


# 2026-08-04 実機調査(不具合1): vLLM/xgrammar の guided decoding が、この
# update_profile のスキーマ+プロンプトの組み合わせで無限空白ループに陥り
# JSON が壊れたまま max_tokens で打ち切られる不具合を実機(127.0.0.1:8000、
# google/gemma-4-31B-it-qat-w4a16-ct)で確認した。schema 側の number の
# minimum/maximum・enum 化・入れ子の複雑さ・temperature のいずれを変えても
# 再現し、guided decoding(response_format)自体を外すと同じ入力から即座に
# 正しい JSON が返ることを確認済み。そのため 1 回目が壊れたら、
# `main_agent._call_main_agent` のような「同じ guided 呼び出しの再試行」
# ではなく、guided を外してテキスト指示で JSON 1 個を書かせる形で再試行する
# (`update_profile.py` の `_generate_update_profile` から使う)。
UPDATE_PROFILE_FALLBACK_NOTE = f"""
【再試行(guided decoding 無し)】
今回は JSON Schema の強制無しで生成します。次の形の JSON オブジェクトを
1 個だけ出力してください。コードフェンス(```)や説明文は付けません。
{{"profile_delta": null または
  {{"interests": {{選好キー({_PREFERENCE_VOCABULARY})の一部を任意で、値は -1.0〜1.0}},
   "party": null または "family_kids"|"couple"|"solo"|"senior"|"group",
   "mobility": null または {_MOBILITY_VOCABULARY} のいずれか,
   "pace": null または "packed"|"relaxed",
   "avoid": [文字列, ...],
   "notes": null または文字列}},
 "score_adjustments": [{{"spot_id": "②の spot_id 語彙のいずれか",
   "delta": -0.5〜0.5 の数値, "why": "理由の短文",
   "handling": "weight"}}, ...](無ければ空配列)}}
"""


MAIN_AGENT_SYSTEM_PROMPT = f"""あなたは鳥海山観光ガイダンスの
ReAct メインエージェントです。
1 周ごとに「考えて、一手打つ」を繰り返します。指定された JSON Schema の
JSON 1 個(thought + action)だけを出力してください。説明文や Markdown は
出しません。

出力フィールドは thought → action の順で埋めます。thought は結論を出す前の
1〜2 文の日本語です。

Tool(action.tool)は次の 6 つです。1 周につき 1 つだけ選びます。
- recommend: おすすめのスポットを探す。args = {{"instruction": 自然文}}。
  件数(k=3)はコードが固定するので書きません。
- plan_itinerary: 旅程がまだ無いときに新規作成する。
  args = {{"days":[{{"date":"YYYY-MM-DD","start":"HH:MM","end":"HH:MM",
  "origin_name":スポット名 または null(**会話に根拠(ユーザーが言った宿・
  地点)があれば必ず書く。無ければ null のままにする** — null で
  precondition_unmet が返ったら下の境界の手順に従う),
  "destination_name":スポット名 または null}}],
  "must_visit":[スポット名,...],
  "candidate_spots":[スポット名,...](入れても入れなくてもよい候補。
  既定空。下記の判断基準を参照),
  "constraints":{{"add":[{{"pred":述語,"args":object,"weight":数値,
  "source_text":根拠になった発話}}],"remove":[制約id,...]}} または null,
  "notes":文字列 または null,
  "assumptions":[確認していない前提の日本語短文,...](無ければ空配列)}}
- edit_itinerary: 既にある旅程を書き換える。
  args = {{"ops":[...(下記)],"constraints":plan_itinerary と同じ形 または null,
  "notes":文字列 または null,
  "allow_refill":真偽値(既定 false。下記の判断基準を参照),
  "assumptions":[文字列,...] または null(null なら基の版からそのままコピー)}}
- search_knowledge: 由来・歴史・注意事項などを調べる。
  args = {{"request":自然文,"spot_name":スポット名 または null}}
- ask_user: ユーザーに聞き返す(結果を左右する情報が不明なときに聞く)。
  args = {{"kind":"preference"|"clarify",
  "slot":kind=preference のとき次のどれか({_SLOT_VOCABULARY}) それ以外は null,
  "surface":kind=clarify のとき聞き返す表層形(元発話の一部) それ以外は null,
  "reason":質問文(専用フォームにそのまま表示される),
  "options":[{{"label":選択肢の表示文,"value":kind=clarify のときスポット名。
  kind=preference のときは選択肢を識別する短い値}}](2〜4 個)}}。
  回答は同じターンの軌跡に返ります(ターンは中断しません)。
- done: このターンで打つ手を終える。args = {{}}。
  done を選んだ後、あなた自身は応答文を書きません(respond が別に書きます)。

境界(必ず守ること):
- あなたはスポットを常に**名前**で扱います。spot_id を見ることも書くこともあり
  ません。旅程・候補・履歴に出てくる地点はすべて名前で書かれています。
  ask_user(kind=clarify)の options[].value もスポット**名**で書いてください
  (コードが解決できなければその質問は実行されません)。
- 1 ターンに recommend / plan_itinerary / edit_itinerary / search_knowledge を
  複数回選べます。旅程の書き換えも 1 ターンに複数回行えます。
- 直前までの軌跡(⑤)を見て、既に得た情報を無駄にせず次の一手を決めます。
  同じ Tool を同じ引数でもう一度選ばないでください(実行されません)。
- ask_user は「**結果を左右する情報が不明**」なときに使います。推測で
  的外れな旅程・提案を返すほうが、質問の手間より体験を損ないます。
- **日付・時間枠**は、**旅程を作る・変える(plan_itinerary/edit_itinerary
  を選ぶ)とき**に会話から読み取れなければ ask_user で聞いてください(薄い
  からといって仮定で進めてはいけません)。推薦だけのターンでは日付は結果に
  影響しないため聞きません。**起点も同様に旅程がらみのときだけの話です**:
  会話に根拠(ユーザーが言った宿・地点)があればそれを使い、根拠が無ければ
  **仮定せず ask_user で聞いてください**(下の plan_itinerary の項も参照)。
- **選好(誰と行くか・歩行耐性・興味など)は recommend のサブエージェントが
  必要に応じて聞きます。**あなたが聞くのは(旅程がらみの)日付・起点・照応
  の曖昧さ(候補が複数の同名地点に解ける・破壊的操作の解釈が割れる等)です。
- 仮定して進めてよいのは次の3つの場合だけです: (1) ユーザーが選定を任せた
  (「お任せ」「おすすめで」等) (2) 結果にほとんど影響しない軽微な隙間
  (3) 質問が未回答(タイムアウト)だった、またはガード等で実行できなかった
  とき(結果にその旨が返ります)。置いた仮定は必ず Tool の結果
  (assumptions)に現れ、最後の応答で必ず説明されます。
- 同じ slot・同じ曖昧さを 2 回聞いてはいけません。既に分かっていること
  (プロフィール・会話に値があるもの)を聞き直してもいけません。
- ask_user は 1 ターンに **6 回まで**です(暴走時の安全弁で、必要な数だけ
  聞けばふつうそれより少なくなります)。
- ask_user の options は**そのまま採用できる具体値だけ**を書きます。起点を
  聞くとき(kind=preference, slot=origin)は、会話・軌跡・現在の旅程に出て
  きた**実在の施設名・駅名**を選択肢に並べてください。「宿」「駅」「その他」
  のような**カテゴリ語・抽象語は選択肢にできません**(自由入力欄が常にある
  ので網羅する必要はありません)。名寄せで解決できない選択肢は送出前に
  取り除かれ、残りが 2 個未満なら質問自体が実行されません。kind=clarify の
  options[].value も同様にスポット名で書いてください(上記のとおり)。
- constraints はあなたが直接書きます。述語(pred)は次の 17 種のどれかです:
  {_PRED_VOCABULARY}
  args の中身は述語ごとに異なります(例: require/exclude/first/last は
  {{"target":スポット名または生タグ}}、time_window は
  {{"target":...,"from":"HH:MM","to":"HH:MM"}} 等)。
  不正な述語・引数は個別に無効化され、結果で報告されます。
- edit_itinerary.ops の op は次の 8 種です(targets/target/with はスポット名):
  add(targets, day?, after?) / remove(targets) / move(target, day?, position?) /
  replace(target, with) / lock(targets, locked) / set_stay(target, min) /
  set_time(target, arrive?, depart?) / revert(to_version?)。
  自然言語の「元に戻して」は ops=[{{"op":"revert"}}] の 1 手にします
  (他の op と混ぜません)。
- edit_itinerary.allow_refill は既定 false です。false のときソルバーは
  ops で指定した変更だけを行い、訪問集合(誰を訪れるか)は変えません。
  ユーザーが「代わりにどこか入れて」「空いた時間に何か足して」「もっと良い
  組み合わせにして」のように**補充・入れ替えを明示的に求めたとき**だけ
  true にしてください。「外して」だけの依頼(補充を求めていない)は
  false のままにします。
- plan_itinerary.must_visit と candidate_spots は役割が異なります。
  **必ず入れたい場所は must_visit** に書きます(ソルバーは必ず組み込みます)。
  **candidate_spots は入れても入れなくてもよい候補**です(時間が余れば
  ソルバーが組み込みます)。ソルバーはこの2つを合わせた集合の外からは
  一切スポットを選びません(constraints の require で明示した場所は例外
  で、集合の外でも必ず入ります)。**ユーザーが具体的なスポット名を挙げておらず
  candidate_spots に入れる候補が必要な場合は、先に recommend を呼んで
  候補を得てから、その候補名を candidate_spots に渡して plan_itinerary を
  呼んでください**(「おすすめで組んで」等)。must_visit と
  candidate_spots が両方とも空のまま plan_itinerary を呼ぶとエラーが
  返ります(空の旅程を作らないため)。
  **candidate_spots の判断基準(必ず守ること)**: ユーザーが具体的なスポット
  名を挙げて追加・訪問を頼んだときは、candidate_spots は**空のまま**にして
  ください(指名された場所は must_visit だけで組みます。指名していない
  場所を候補として混ぜてはいけません)。candidate_spots に入れてよいのは、
  ユーザーが「おすすめで埋めて」「空いた時間は任せる」のように**選定を
  任せた**場合に、直前の recommend の結果(このターンの軌跡に載っている
  スポット名)から**転記した名前だけ**です。自分の知識やユーザーが言って
  いない地名を candidate_spots に創作して入れてはいけません。
- plan_itinerary.assumptions には、日付・起点など**ユーザーに確認していない
  前提**を日本語短文で必ず列挙してください(例:「日付は明日と仮定」
  「起点は直前に話題に出た宿泊施設と仮定」)。何も仮定していなければ
  空配列にします。
  edit_itinerary.assumptions は既定で前の版からそのままコピーされるので、
  ユーザーが日付や起点を新たに明示するなど**前提が解消されたときだけ**、
  新しい内容(または解消済みなら空配列)を書いて置き換えてください。
- plan_itinerary で起点が未指定のエラー(precondition_unmet、「起点が未指定
  です」)が返ったら、まず ask_user で起点を尋ねてください。聞かない/聞け
  ない場合は、会話に出た地点名を origin_name に明示して plan_itinerary を
  再実行し、その仮定を assumptions に書いてください。起点を勝手に選んだ
  まま黙って進めてはいけません。
- 十分な情報が揃ったら done を選んでターンを終えてください。
"""


# dialogue_style.md §4「新 RESPOND_SYSTEM_PROMPT 案(全文)」(2026-08-04
# 決定稿)をそのまま採用する。旧プロンプト(様式・分量の規定が無く、
# 「考慮した条件・仮定・譲歩を必ず列挙」という網羅性だけを強制していた版)は
# 報告書調・列挙調の直接原因だった(同文書 §1)。
RESPOND_SYSTEM_PROMPT = """\
あなたは鳥海山エリア専門の親切なツアーガイドです。いまシステムが実行した手と
その結果(軌跡)・素材・会話履歴をもとに、ユーザーへの応答を 1 回だけ書きます。

書き方:
- 敬体で、ガイドとして自然に語りかけます。Markdown(見出し・箇条書き・太字)は
  内容が伝わる範囲で使います。絵文字は、見出しやスポット紹介など内容の理解を
  助けるところで控えめに使ってかまいません(1 文ごとに付けるのは過剰です)。
- 次の順で構成します:
  1. 要望をどう受け取り何をしたかを 1〜2 文で。ユーザーの言葉(好み・条件)を
     言い換えて反映します。
  2. 本体:
     - おすすめを出したターンでは、提示した 3 件**すべて**について、各スポット
       が「なぜこの方に合うか」「何が見どころか」を素材の範囲で 1〜3 文ずつ
       紹介します(一番の推し 1 件に厚みをつけてかまいません)。
     - 旅程を作成・変更したターンでは、行程を上から全部読み上げません
       (旅程カードが表示されています)。1 日の流れの要点と、変更した点・
       工夫した点を紹介します。
     - 質問に答えるターンでは、検索結果の範囲で具体的に答えます。
  3. 反映できなかった要望・解決できなかった項目・エラーがあれば必ず伝えます
     (無言で捨てません)。判断に影響する仮定(日付・起点など)は自然な文で
     一言添えます(箇条書きの列挙にしません。詳細はカードに表示されています)。
  4. 最後に必ず、次にできることを具体的な質問 1〜2 個で提案します
     (例: 「丸池様を旅程に加えますか? それとも他の水辺の候補もご覧に
     なりますか?」)。提案は、いま提示している候補・旅程・この場でできる操作の
     範囲に限ります。

必須規則(書き方より優先):
- 軌跡・素材・現在の旅程・会話履歴にある事実(スポット名、時刻、件数)だけを
  使います。無い事実を作りません。素材にない魅力・設備・混雑状況を推測で
  書きません。
- 候補や旅程を組み替えません。実行済みの結果を説明するだけです。
- 内部の実装語を出しません: ツール名・処理ステップ名・spot_id・タグや
  プロフィールの英語コード(nature, mobility など)・「検索を実施しました」の
  ような処理の自己言及。
- mode が failure のときは、うまく処理できなかったことと、次にユーザーが
  できること(言い換え・条件を絞る等)を短く伝えます。
"""


def build_update_profile_messages(
    state: TurnState,
    *,
    now: datetime | None = None,
    utterance_override: str | None = None,
) -> list[dict[str, str]]:
    """N1.5 `update_profile` 用のプロンプト。会話履歴 + 最新発話が入力である。

    `utterance_override` は `ask_user` の回答に対して本ステップをターン内で
    もう 1 回走らせるとき(§2・§7)に使う。`state.utterance`(このターンの
    元発話)自体は書き換えない — メインループの ⑥ はターンを通じて元発話の
    ままにする(質問と回答は既に軌跡/履歴に残るため)。
    """

    del now  # 日付情報は不要(understand/respond と異なり期日解釈をしない)
    utterance = utterance_override if utterance_override is not None else state.utterance
    vocab = [
        {"spot_id": spot_id, "name_ja": state.spot_names.get(spot_id, spot_id)}
        for spot_id in state.spot_id_vocab
    ]
    dynamic = "\n".join(
        [
            "① 現在のプロフィール:\n" + _compact_json(state.profile.model_dump(mode="json")),
            "② 参照可能な spot_id 語彙:\n" + _compact_json(vocab),
            "③ 会話履歴:\n" + (state.history or "(なし)"),
            "④ ユーザーの発話:\n" + utterance,
        ]
    )
    return [
        {"role": "system", "content": UPDATE_PROFILE_SYSTEM_PROMPT},
        {"role": "user", "content": dynamic},
    ]


def build_update_profile_fallback_messages(
    messages: list[dict[str, str]], *, reason: str
) -> list[dict[str, str]]:
    """guided decoding が壊れたときの再試行用メッセージ(不具合1の是正)。

    `build_update_profile_messages` が組み立てた ①〜④ の内容はそのまま
    保ち、guided decoding を外す旨とスキーマの説明をテキストで追記する。
    `UPDATE_PROFILE_FALLBACK_NOTE` の由来は同モジュールの調査コメントを
    参照。
    """

    retry_messages = [dict(value) for value in messages]
    retry_messages[-1] = dict(retry_messages[-1])
    retry_messages[-1]["content"] += (
        f"\n\n【1 回目の失敗理由】{reason[:240]}\n" + UPDATE_PROFILE_FALLBACK_NOTE
    )
    return retry_messages


def update_profile_guided_schema(spot_ids: list[str]) -> dict[str, Any]:
    """N1.5 `update_profile` の guided JSON schema。`uniqueItems` は使わない。"""

    spot_value_schema: dict[str, Any]
    if spot_ids:
        spot_value_schema = {"type": "string", "enum": list(dict.fromkeys(spot_ids))}
    else:
        spot_value_schema = {"type": "string"}
    return {
        "type": "object",
        "properties": {
            "profile_delta": _profile_delta_schema(),
            "score_adjustments": _score_adjustments_schema(
                spot_value_schema, enabled=bool(spot_ids)
            ),
        },
        "required": ["profile_delta", "score_adjustments"],
        "additionalProperties": False,
    }


def build_main_agent_messages(
    state: TurnState,
    *,
    reduced: bool,
    system_note: str | None = None,
    now: datetime | None = None,
) -> list[dict[str, str]]:
    """①〜⑥を §3.1 の順で組み立てる。①(system)はターン間で byte 同一。"""

    active_constraints = (
        state.itinerary.constraints if state.itinerary is not None else state.pending_constraints
    )
    itinerary_digest = format_itinerary_digest(
        state.itinerary.itinerary if state.itinerary is not None else None,
        spot_names=state.spot_names,
    )
    constraints_digest = format_active_constraints(
        active_constraints, spot_names=state.spot_names
    )
    sections = [
        "② プロフィール:\n" + _compact_json(state.profile.model_dump(mode="json")),
        "③ 今日の日付(JST)・現在の旅程・有効な制約:\n"
        + _date_context(now)
        + "\n"
        + itinerary_digest
        + "\n有効な制約:\n"
        + _compact_json(constraints_digest),
        "④ 会話履歴:\n" + (state.history or "(なし)"),
        "⑤ このターンの軌跡:\n" + trajectory_text(state.trajectory),
    ]
    if reduced:
        sections.append(
            "【システム指示】手数またはコンテキスト予算の上限に達しました。"
            "まとめに入ってください。次の一手は done のみ選べます。"
        )
    if system_note:
        sections.append(f"【補足】{system_note}")
    # ⑥の発話より後ろには一切追加しない。
    sections.append("⑥ ユーザーの発話:\n" + state.utterance)
    return [
        {"role": "system", "content": MAIN_AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(sections)},
    ]


def build_respond_messages(
    state: TurnState,
    *,
    mode: ResponseMode,
    materials: Mapping[str, SpotMaterial] | None = None,
    material_spot_ids: Sequence[str] = (),
) -> list[dict[str, str]]:
    """①〜⑤を dialogue_style.md §4 の順で組み立てる。

    `materials`/`material_spot_ids` は `respond.py` が組み立てる(§4 論点 A2:
    素材はメインループには渡さず、respond の入力にだけ足す)。呼び出し元が
    省略した場合(既存呼び出し・素材が無いターン)は「(なし)」になる。
    """

    degraded_json = _compact_json(
        [value.model_dump(mode="json") for value in state.degraded]
    )
    dynamic = "\n\n".join(
        [
            f"mode: {mode.value}",
            "① このターンの軌跡:\n" + trajectory_text(state.trajectory),
            "② 譲歩・縮退:\n" + degraded_json,
            "③ 会話履歴:\n" + (state.history or "(なし)"),
            "④ ユーザーの発話:\n" + state.utterance,
            "⑤ 素材(このターンで提示したスポットの説明):\n"
            + format_materials_section(
                material_spot_ids, materials or {}, state.spot_names
            ),
        ]
    )
    return [
        {"role": "system", "content": RESPOND_SYSTEM_PROMPT},
        {"role": "user", "content": dynamic},
    ]


def format_materials_section(
    spot_ids: Sequence[str],
    materials: Mapping[str, SpotMaterial],
    spot_names: Mapping[str, str],
) -> str:
    """⑤ 素材節の本文。`- {名前}: {description} / {social_proof} / タグ: {tags_ja}`

    の行列挙(dialogue_style.md §4)。`spot_ids` はこのターンで提示した
    スポットに限定済みの前提(`respond._presented_material_spot_ids` が上限
    `RESPOND_MATERIALS_MAX_SPOTS` 件まで絞る)。

    2026-08-04 レビュー是正(L-6): `spot_names` に無い spot_id は行ごと
    スキップする(元は生の `spot_id` をそのまま名前欄に出していたが、respond
    の入力にコード内部識別子を漏らさないため)。
    """

    if not spot_ids:
        return "(なし)"
    lines: list[str] = []
    for spot_id in spot_ids:
        name = spot_names.get(spot_id)
        if name is None:
            continue
        material = materials.get(spot_id)
        parts: list[str] = []
        description = material.description if material is not None else None
        if description:
            if len(description) > RESPOND_MATERIAL_DESCRIPTION_MAX_CHARS:
                description = description[:RESPOND_MATERIAL_DESCRIPTION_MAX_CHARS] + "…"
            parts.append(description)
        if material is not None and material.social_proof:
            parts.append(material.social_proof)
        if material is not None and material.tags_ja:
            parts.append("タグ: " + "、".join(material.tags_ja))
        detail = " / ".join(parts) if parts else "(説明なし)"
        lines.append(f"- {name}: {detail}")
    return "\n".join(lines) if lines else "(なし)"


def main_agent_guided_schema(
    constraint_ids: list[str] | None = None,
    *,
    allow_ask_user: bool = True,
) -> dict[str, Any]:
    """xgrammar 互換の schema。分岐ごとに `tool` の enum を排他にする。

    `uniqueItems` は意図的に一切使わない(xgrammar 未実装のため)。
    `allow_ask_user=False` は R4(§10)の上限に達したとき、呼び出し元
    (`main_agent.py`)がこの周だけ `ask_user` を分岐から外すために使う。
    """

    branches = [
        _tool_action_schema("recommend", _recommend_args_schema()),
        _tool_action_schema("plan_itinerary", _plan_itinerary_args_schema(constraint_ids)),
        _tool_action_schema("edit_itinerary", _edit_itinerary_args_schema(constraint_ids)),
        _tool_action_schema("search_knowledge", _search_knowledge_args_schema()),
    ]
    if allow_ask_user:
        branches.append(_tool_action_schema("ask_user", _ask_user_args_schema()))
    branches.append(_tool_action_schema("done", _empty_args_schema()))
    return {
        "type": "object",
        "properties": {
            "thought": {"type": "string", "minLength": 1},
            "action": {"anyOf": branches},
        },
        "required": ["thought", "action"],
        "additionalProperties": False,
    }


def main_agent_done_only_schema() -> dict[str, Any]:
    """R1/R2 到達時に切り替える縮小スキーマ(`done` のみ)。"""

    return {
        "type": "object",
        "properties": {
            "thought": {"type": "string", "minLength": 1},
            "action": _tool_action_schema("done", _empty_args_schema()),
        },
        "required": ["thought", "action"],
        "additionalProperties": False,
    }


def _tool_action_schema(tool: str, args_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "enum": [tool]},
            "args": args_schema,
        },
        "required": ["tool", "args"],
        "additionalProperties": False,
    }


def _empty_args_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {}, "additionalProperties": False}


def _nullable_string(*, min_length: int = 0) -> dict[str, Any]:
    string_schema: dict[str, Any] = {"type": "string"}
    if min_length:
        string_schema["minLength"] = min_length
    return {"anyOf": [{"type": "null"}, string_schema]}


def _recommend_args_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"instruction": {"type": "string", "minLength": 1}},
        "required": ["instruction"],
        "additionalProperties": False,
    }


def _search_knowledge_args_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "request": {"type": "string", "minLength": 1},
            "spot_name": _nullable_string(min_length=1),
        },
        "required": ["request", "spot_name"],
        "additionalProperties": False,
    }


def _ask_user_option_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "label": {"type": "string", "minLength": 1},
            "value": {"type": "string", "minLength": 1},
        },
        "required": ["label", "value"],
        "additionalProperties": False,
    }


def _ask_user_args_schema() -> dict[str, Any]:
    """`kind` ごとの anyOf 分岐で `slot`/`surface` の排他をスキーマ側で強制する。

    2026-08-04 実機再現([25 §1-6](../../../../Docs/25_known_issues.md)):
    旧スキーマは `kind`/`slot`/`surface` を独立フィールドとして許す平坦な
    構成だったため、LLM が `kind="clarify"` に `slot` 非 null を書ける組み
    合わせを guided decoding が生成し得た。`AskUserArgs.model_validate` の
    ValidationError が `_dispatch` の包括 except で recoverable=false の
    INTERNAL に化けてターン全体を落としていた。メインループの Tool 分岐
    (`main_agent_guided_schema`)と同じ「kind ごとに anyOf で分ける」
    パターンで、生成時点から不正な組み合わせを排除する(§14 の型定義
    `AskUserArgs.validate_kind_shape` が正)。
    """

    def _common_properties() -> dict[str, Any]:
        # 2026-08-04、レビュー是正(L-5): 呼ぶたびに新しい dict を返す。
        # 呼び出し元で 1 回だけ作って `**` で両分岐に展開すると、"options"
        # (と入れ子の `_ask_user_option_schema()`)が両分岐で同一オブジェクト
        # になり、片方を書き換えるともう片方まで変わってしまう。
        return {
            "reason": {"type": "string", "minLength": 1},
            "options": {
                "type": "array",
                "items": _ask_user_option_schema(),
                "minItems": 2,
                "maxItems": 4,
            },
        }

    preference_branch = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["preference"]},
            "slot": {"type": "string", "enum": slot_values()},
            "surface": {"type": "null"},
            **_common_properties(),
        },
        "required": ["kind", "slot", "surface", "reason", "options"],
        "additionalProperties": False,
    }
    clarify_branch = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["clarify"]},
            "slot": {"type": "null"},
            "surface": {"type": "string", "minLength": 1},
            **_common_properties(),
        },
        "required": ["kind", "slot", "surface", "reason", "options"],
        "additionalProperties": False,
    }
    return {"anyOf": [preference_branch, clarify_branch]}


def _constraint_add_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "pred": {"type": "string", "enum": pred_values()},
            "args": {"type": "object", "additionalProperties": True},
            "weight": {"type": "number", "minimum": 0.0},
            "source_text": {"type": "string"},
        },
        "required": ["pred", "args", "weight", "source_text"],
        "additionalProperties": False,
    }


def _constraint_ops_schema(constraint_ids: list[str] | None) -> dict[str, Any]:
    normalized_ids = list(dict.fromkeys(constraint_ids or []))
    remove_item_schema: dict[str, Any]
    if normalized_ids:
        remove_item_schema = {"type": "string", "enum": normalized_ids}
    else:
        remove_item_schema = {"type": "string"}
    return {
        "type": "object",
        "properties": {
            "add": {
                "type": "array",
                "items": _constraint_add_schema(),
                "maxItems": MAIN_AGENT_MAX_CONSTRAINTS_ADD,
            },
            "remove": {
                "type": "array",
                "items": remove_item_schema,
                "maxItems": MAIN_AGENT_MAX_CONSTRAINTS_REMOVE if normalized_ids else 0,
            },
        },
        "required": ["add", "remove"],
        "additionalProperties": False,
    }


def _nullable_constraints_schema(constraint_ids: list[str] | None) -> dict[str, Any]:
    return {"anyOf": [{"type": "null"}, _constraint_ops_schema(constraint_ids)]}


def _assumptions_schema() -> dict[str, Any]:
    """未確認の前提(日付・起点等)の日本語短文リスト(2026-08-04 追加。

    Docs/30_design/agent_react_architecture.md §5)。
    """

    return {
        "type": "array",
        "items": {"type": "string", "minLength": 1},
        "maxItems": MAIN_AGENT_MAX_ASSUMPTIONS,
    }


def _nullable_assumptions_schema() -> dict[str, Any]:
    """`edit_itinerary.assumptions`: null = 基の版からそのままコピー。"""

    return {"anyOf": [{"type": "null"}, _assumptions_schema()]}


def _plan_day_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "date": {"type": "string", "minLength": 1},
            "start": {"type": "string", "minLength": 1},
            "end": {"type": "string", "minLength": 1},
            "origin_name": _nullable_string(min_length=1),
            "destination_name": _nullable_string(min_length=1),
        },
        "required": ["date", "start", "end", "origin_name", "destination_name"],
        "additionalProperties": False,
    }


def _plan_itinerary_args_schema(constraint_ids: list[str] | None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "days": {
                "type": "array",
                "items": _plan_day_schema(),
                "minItems": 1,
                "maxItems": MAIN_AGENT_MAX_DAYS,
            },
            "must_visit": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "maxItems": MAIN_AGENT_MAX_MUST_VISIT,
            },
            # 入れても入れなくてもよい候補(既定空。2026-08-04 夜追加、
            # ADR-0022)。string 配列なので xgrammar の「配列要素内の number」
            # 既知不具合([25 §2-1])には該当しない。
            "candidate_spots": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "maxItems": MAIN_AGENT_MAX_CANDIDATE_SPOTS,
            },
            "constraints": _nullable_constraints_schema(constraint_ids),
            "notes": _nullable_string(),
            "assumptions": _assumptions_schema(),
        },
        "required": [
            "days",
            "must_visit",
            "candidate_spots",
            "constraints",
            "notes",
            "assumptions",
        ],
        "additionalProperties": False,
    }


def _op_schema(op: str, properties: dict[str, Any]) -> dict[str, Any]:
    schema_properties = {"op": {"type": "string", "enum": [op]}, **properties}
    return {
        "type": "object",
        "properties": schema_properties,
        "required": list(schema_properties),
        "additionalProperties": False,
    }


def _name_array_schema() -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1}


def _nullable_day_number() -> dict[str, Any]:
    return {"anyOf": [{"type": "null"}, {"type": "integer", "minimum": 1}]}


def _edit_ops_schema() -> dict[str, Any]:
    add_op = _op_schema(
        "add",
        {
            "targets": _name_array_schema(),
            "day": _nullable_day_number(),
            "after": _nullable_string(min_length=1),
        },
    )
    remove_op = _op_schema("remove", {"targets": _name_array_schema()})
    move_op = _op_schema(
        "move",
        {
            "target": {"type": "string", "minLength": 1},
            "day": _nullable_day_number(),
            "position": _nullable_day_number(),
        },
    )
    replace_op = _op_schema(
        "replace",
        {
            "target": {"type": "string", "minLength": 1},
            "with": {"type": "string", "minLength": 1},
        },
    )
    lock_op = _op_schema(
        "lock",
        {"targets": _name_array_schema(), "locked": {"type": "boolean"}},
    )
    set_stay_op = _op_schema(
        "set_stay",
        {"target": {"type": "string", "minLength": 1}, "min": {"type": "integer", "minimum": 1}},
    )
    set_time_op = _op_schema(
        "set_time",
        {
            "target": {"type": "string", "minLength": 1},
            "arrive": _nullable_string(min_length=1),
            "depart": _nullable_string(min_length=1),
        },
    )
    revert_op = _op_schema("revert", {"to_version": _nullable_day_number()})
    return {
        "anyOf": [
            add_op,
            remove_op,
            move_op,
            replace_op,
            lock_op,
            set_stay_op,
            set_time_op,
            revert_op,
        ]
    }


def _edit_itinerary_args_schema(constraint_ids: list[str] | None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "ops": {
                "type": "array",
                "items": _edit_ops_schema(),
                "minItems": 1,
                "maxItems": MAIN_AGENT_MAX_OPS,
            },
            "constraints": _nullable_constraints_schema(constraint_ids),
            "notes": _nullable_string(),
            "allow_refill": {"type": "boolean"},
            "assumptions": _nullable_assumptions_schema(),
        },
        "required": ["ops", "constraints", "notes", "allow_refill", "assumptions"],
        "additionalProperties": False,
    }


def _profile_delta_schema() -> dict[str, Any]:
    profile_properties = {
        key: {"type": "number", "minimum": -1.0, "maximum": 1.0}
        for key in preference_values()
    }
    return {
        "anyOf": [
            {"type": "null"},
            {
                "type": "object",
                "properties": {
                    "interests": {
                        "type": "object",
                        "properties": profile_properties,
                        "additionalProperties": False,
                    },
                    "party": {
                        "anyOf": [
                            {"type": "null"},
                            {
                                "type": "string",
                                "enum": [
                                    "family_kids",
                                    "couple",
                                    "solo",
                                    "senior",
                                    "group",
                                ],
                            },
                        ]
                    },
                    "mobility": {
                        "anyOf": [
                            {"type": "null"},
                            {
                                "type": "string",
                                "enum": [
                                    "avoid_walk",
                                    "short_walk_ok",
                                    "hike_ok",
                                ],
                            },
                        ]
                    },
                    "pace": {
                        "anyOf": [
                            {"type": "null"},
                            {"type": "string", "enum": ["packed", "relaxed"]},
                        ]
                    },
                    "avoid": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "maxItems": 8,
                    },
                    "notes": {"anyOf": [{"type": "null"}, {"type": "string"}]},
                },
                "required": [
                    "interests",
                    "party",
                    "mobility",
                    "pace",
                    "avoid",
                    "notes",
                ],
                "additionalProperties": False,
            },
        ]
    }


def _score_adjustments_schema(
    spot_value_schema: dict[str, Any], *, enabled: bool
) -> dict[str, Any]:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "spot_id": spot_value_schema,
                "delta": {"type": "number", "minimum": -0.5, "maximum": 0.5},
                "why": {"type": "string"},
                "handling": {"type": "string", "enum": ["weight"]},
            },
            "required": ["spot_id", "delta", "why", "handling"],
            "additionalProperties": False,
        },
        "maxItems": 8 if enabled else 0,
    }


def trajectory_text(trajectory: Sequence[TrajectoryStep]) -> str:
    if not trajectory:
        return "(まだありません)"
    lines: list[str] = []
    for index, step in enumerate(trajectory, 1):
        lines.append(f"[手{index}] tool={step.tool}")
        lines.append(f"  thought: {step.thought}")
        lines.append(f"  args: {_compact_json(step.args)}")
        lines.append(f"  observation: {step.observation}")
        if step.error is not None:
            lines.append(f"  error: {_compact_json(step.error)}")
    return "\n".join(lines)


def _date_context(now: datetime | None) -> str:
    current = now or datetime.now(_JAPAN_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_JAPAN_TZ)
    else:
        current = current.astimezone(_JAPAN_TZ)
    today = current.date()
    tomorrow = today + timedelta(days=1)
    weekday = _WEEKDAYS_JA[today.weekday()]
    return (
        f"今日は {today.isoformat()}({weekday})です。"
        f"『明日』は {tomorrow.isoformat()} を指します。"
    )


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
