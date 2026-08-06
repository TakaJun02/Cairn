# ADR-0009: 旅程計画エージェントにサブエージェントを持たせない

- 状態: **廃止 (superseded by [ADR-0019](0019-react-main-agent-subagents.md)、2026-08-04)**(旧: 承認 2026-07-31 / 適用範囲を限定 2026-08-01、[ADR-0011](0011-knowledge-search-subagent.md))
- 日付: 2026-07-31 / 改訂 2026-08-01
- 関係: [ADR-0008](0008-plan-then-execute.md)(一括プラン方式)の帰結を明文化するもの。置き換えではない

> **2026-08-01 改訂: 適用範囲を「メインエージェントの 1 ターンの逐次連鎖」に限定した。**
> 本 ADR が下記「覆す条件」に挙げた 3 項目のすべてに知識検索が該当したため、**知識検索だけは
> 独立したサブエージェント(Agent as a Tool)として切り出す**([ADR-0011](0011-knowledge-search-subagent.md))。
>
> | 引き続き禁止 | 例外として許可 |
> | --- | --- |
> | **メインの `understand` → `act` → `respond` を別エージェントに分割すること。**共有状態(プロファイル・旅程・候補)の上の依存連鎖であり、下記の理由 1〜6 がそのまま効く | **独立した調査ドメインの切り出し。**共有状態を必要とせず、入力と出力だけで完結し、中間状態がメインのコンテキストに収まらないもの |
>
> 本文の理由 1〜6 は**メインについての評価として引き続き有効**である。理由 1 が引用した Anthropic の
> 原文のうち "information that exceeds single context windows" と "interfacing with numerous complex tools"
> は、**知識検索については当てはまっていた**(ADR-0011 §理由 2)。

## 文脈(何が問題か)

[30_design/agent_planning_phase.md](../30_design/agent_planning_phase.md) の設計では、1 ターンの LLM 呼び出しは 2〜3 回で、いずれも「1 回きりの構造化出力」である。自分のコンテキストと Tool を持ち自分で反復する下位主体、すなわちサブエージェントは 1 つも存在しない。

これは意図した結果だが、**明文化されていなかった。**2024〜2026 年のエージェント設計はマルチエージェント構成が主流の話題であり、「なぜ分けないのか」は今後くり返し問われる。実装者(Codex)が「理解・計画・説明を別エージェントに分けたほうがよいのでは」と判断する余地も残る。

検討した切り出し候補は 4 つあった。

1. `understand` を「意図・plan・照応」と「選好・制約の抽出」の 2 ノードに分け、**並列**に実行する
2. 理解 / 計画 / 説明を直列の専門エージェントに分ける(PURE 型)
3. ターン外の非同期エージェント(プロファイル整理・`unmodeled` 分析)
4. `respond` の出力を検証するサブエージェント

## 決定(何をすると決めたか)

**サブエージェントを切り出さない。**LLM 呼び出しはすべて「専門呼び出し」(Tool を持たず反復もしない、役割が 1 つに固定された構造化出力)にとどめる。

- **案 2・4 は採らない**(下記の理由)
- **案 3 は採るが、エージェントにしない。**`unmodeled` の分析などは CLI コマンド(`python -m app.cli analyze-unmodeled`)として実装する
- **案 1 だけは将来の選択肢として残す。**ただし現時点では採らず、抽出品質が実測で不足した場合にのみ検討する。移行を安くするため、`constraints` などのターン全体の値は `plan` の引数に埋めず出力の直下に置く(同文書 §18.1)

**この決定を覆す条件**を先に決めておく。次のいずれかが起きたときに再検討する。

- 1 ターンで独立した調査方向を多数探索する必要が生じた
- 16K のコンテキストに情報が収まらず、別コンテキストでの圧縮が品質を実測で改善した
- Tool 数と専門領域が大幅に増え、単一プロンプトでは Tool 選択の精度が保てなくなった

> **2026-08-01: 知識検索がこの 3 条件すべてに該当した。**先に条件を書いておいたことが、その後の判断を
> 「気分で覆した」ではなく「予告した条件に当たった」にした。経緯は [ADR-0011](0011-knowledge-search-subagent.md) §理由 1。

## 理由(なぜ他案でなくこれか)

**1. マルチエージェントが効く条件を、本件はどれも満たさない。**

Anthropic のマルチエージェント研究システムの報告(一次資料で確認済み、2026-07-31):

> "We've found that **multi-agent systems excel at valuable tasks that involve heavy parallelization, information that exceeds single context windows, and interfacing with numerous complex tools.**"

本件は Tool 5 個、1 ターン最大 3 手、POI 43 件である。並列性も、コンテキスト超過も、多数の複雑な Tool もない。

**2. むしろ「効かない条件」に当てはまる。**同じ記事の原文:

> "**some domains that require all agents to share the same context or involve many dependencies between agents are not a good fit for multi-agent systems today.**"

本件の 1 ターンは、プロファイル・旅程・候補という共有状態の上で、推薦の結果を旅程編集に渡す**依存関係の連鎖**である。まさにこの記述に該当する。

**3. 逐次計画タスクでは、分けると悪化するという実測がある。**

*"Towards a Science of Scaling Agent Systems"* (arXiv:2512.08296、一次資料で確認済み)は、260 構成 × 6 ベンチマーク × 3 モデル系列で単一エージェントと 4 種のマルチエージェント構成を比較し、次を報告している。

> "**Agent effectiveness depends on alignment between coordination and task structure, and that mismatched coordination degrades the performance.**"

単一エージェント比の性能変化は **分解可能な金融推論で +80.8%、逐次計画で −70.0%**。**本件の 1 ターンは典型的な逐次計画である。**

**4. マルチエージェントは失敗率が高い。**MAST (arXiv:2503.13657、本文で確認済み)は 7 フレームワーク・1,600 件超の trace を分析し、14 の失敗モードを 3 分類に整理したうえで "Our empirical analysis reveals **41% to 86.7% failure rate** on 7 state-of-the-art (SOTA) open-source MAS" と報告している。失敗モードには役割・指示違反、情報の無視、重複作業、履歴喪失が含まれる — **いずれも現在は単一 JSON の中で構造的に起きない問題である。**

**5. 案 2 は NFR-3 に正面から反する。**直列に分ければ 1 ターンの LLM 呼び出しが 4〜5 回になる。[30_design/recommendation_planning.md §3.1](../30_design/recommendation_planning.md) が既に「PURE 型を素直に実装すると 1 ターン 3 呼び出しになるので `understand` に相乗りさせる」と判断している。

**6. 案 4 はコードで代替できる。**検証内容は「出していない `spot_id` の表示名が本文に出ていないか」であり、DB の表示名との文字列照合で足りる。LLM を使う理由がない。

**7. 案 3 をエージェントにしないのは、再現性のため。**バックグラウンドで状態が変わる仕組みは、実験の再現性(NFR-2)と 1 人開発での把握しやすさ(NFR-1)を損なう。CLI コマンドなら明示的に走らせられる。

## 影響(この決定で生じる制約・やること)

- **`domains/conversation/` の下にエージェントの入れ子を作らない。**LLM を呼ぶのは `understand.py` / `respond.py` と、Tool の内側の専門呼び出しだけ
- **Tool の内側の LLM 呼び出しは 1 ターン 1 回まで**という予算(P7)を維持する
- **`understand` の出力のうちターン全体に効く値(`constraints` など)は `plan` の引数に埋めない。**将来案 1 に移るとき、プロンプトを分割するだけで済むようにするため
- `unmodeled` の分析は CLI コマンドとして実装する
- **この決定は「今の規模での判断」である。**上記の「覆す条件」に当たったかどうかは、計測ではなく**実際に使っていて気づくか**で判断する(2026-08-01: NFR-7 削除により `turn_metrics` を廃止)
