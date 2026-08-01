# AI エージェントのアーキテクチャパターン — 調査と、現行設計への適用

- 状態: **決定稿(2026-07-31、§6 の 6 論点が決着)**
- 日付: 2026-07-31
- 位置づけ: [agent_planning_phase.md](agent_planning_phase.md) で固めたエージェント設計に、**ドメイン非依存の「エージェントの作り方」の知見**を当て、改善余地を洗い出す
- 対象外: **旅行プランニング・旅程生成・POI 推薦・対話型推薦の研究**([recommendation_planning.md](recommendation_planning.md) §2 で調査済み)
- 実施: 調査を Codex に委譲(`gpt-5.6-sol` / effort `max`、24 分)→ **Claude が一次資料を検証**([/CLAUDE.md](../../CLAUDE.md) の役割分担)

> **§0 に結論がある。実装や文書修正に着手するときはそこと §3 を読めばよい。**
>
> ⚠️ **§0.2 の改善候補と §2〜§3 の「現行」という記述は、調査時点(2026-07-31 午前)のものである。**その後 §6 の決着を受けて [agent_planning_phase.md](agent_planning_phase.md) に**反映済み**なので、**現在の設計は同文書 §23・§24 が正**。本文書は「なぜそう変えたか」の根拠として読む。

---

## 0. 結論

### 0.1 最も重要な発見 3 つ

1. **現行方式には名前と原典があった。**`$N` 参照つきの一括プランをコードが解決して実行する方式は **LLMCompiler**(ICML 2024)そのものである。**独自方式ではないので、原典の知見をそのまま取り込める**
2. **`understand` のスキーマの「フィールドの並び順」が抽出品質を左右する可能性が高い。**JSON のキー順が生成順を強制し、結論を先に書かせると推論が起きないことが実証されている。**現行スキーマは結論(`intent`/`plan`)が先頭にあり、まさにその並びである**
3. **「サブエージェントを切り出さない」は、支持証拠が付いたどころか、切り出すと悪化する側の証拠が出た。**逐次依存が中心のタスクでは単一エージェント比 **−70.0%** という報告がある

### 0.2 改善候補(優先順位)

| 順位 | 改善候補 | どの文書のどこを変えるか | 根拠の強さ | コスト |
| --- | --- | --- | --- | --- |
| **1** | **`understand` の出力スキーマのフィールド順を入れ替える**(根拠 → 結論) | `agent_planning_phase.md` §3.1 | **強**(§2.2 で検証済み) | **低** |
| **2** | **スキーマを「静的な契約」でなく「LLM 向けに最適化する対象」として作り直す**(説明の追加・平坦化・相互排他の明示) | 同 §3.1 / `understand_node.md` | 中〜強 | 低〜中 |
| **3** | **抽出の固定評価集合を作る**(1・2 の効果を測る手段。これがないと変えても分からない) | 同 §10 / `recommendation_planning.md` §7 層 1 | — (前提) | 中 |
| **4** | **`$N` の検証を強化する**(型整合・循環検出・Tool 入出力の互換性) | 同 §1.3 **P4** | 中(原典の設計に基づく) | 低 |
| **5** | **反復ループ対策を縮退表に入れる**(`max_tokens` / タイムアウト / 反復検知) | 同 §9 | **強**(我々のモデル系列の実 issue) | 低 |
| **6** | **固定プレフィックスを先頭に集める**(prefix cache と "lost in the middle" の両方に効く) | 同 §7 | 中 | 低 |
| 7 | **検証失敗時に限り 1 回だけ再計画する** | 同 §15.3 / §15.6(**グラフが変わる**) | 中 | 中 |
| 8 | **独立した手だけ限定的に並列実行する**(LLM 呼び出しの並列化とは分ける) | 同 §16.4 | 中 | 中 |
| 9 | **ノード・Tool を span 化する** | 同 §10 | 中 | 中 |

**1・2・4・5・6 は低コストで、いま文書を直すだけで済む。7・8 はアーキテクチャが変わるので判断が要る**(§6)。

---

## 1. 調査の方法と、この文書の読み方

- 調査そのものは Codex が実施(454 行の報告)。**そのうち設計を変える根拠になる主張について、Claude が一次資料に当たって突き合わせた**
- 検証の結果、**3 件のズレがあった**(§5)。したがって本文には**検証できたものだけ**を書く

| 記号 | 意味 |
| --- | --- |
| ✅ | **Claude が一次資料で原文・数値を確認した**(2026-07-31) |
| ⚠️ | 抄録では確認できず、**本文まで当たって確認**した |
| ❌ | **確認できなかった。**§5 に隔離し、本文の根拠にしない |

---

## 2. 調査結果

### 2.1 制御構造 — 現行方式は「LLMCompiler 型」だった

#### ✅ LLMCompiler(ICML 2024)

- Sehoon Kim, Suhong Moon, Ryan Tabrizi, Nicholas Lee, Michael W. Mahoney, Kurt Keutzer, Amir Gholami, *"An LLM Compiler for Parallel Function Calling"*, **ICML 2024** (PMLR 235). https://proceedings.mlr.press/v235/kim24y.html
- 抄録原文(確認済み):
  > "However, current methods for function calling often require sequential reasoning and acting for each function which can result in high latency, cost, and sometimes inaccurate behavior. **To address this, we introduce LLMCompiler, which executes functions in parallel to efficiently orchestrate multiple function calls.**"
  > "We observe consistent **latency speedup of up to 3.7×, cost savings of up to 6.7×, and accuracy improvement of up to ~9%** compared to ReAct."

**本件との関係。**Planner が依存関係つきのタスク列を出し、後続タスクの入力を `$1` のような**変数で表し**、依存が解決してから Executor に渡す — これは [agent_planning_phase.md §1.2](agent_planning_phase.md) の `$1.spot_ids` と同型である。

- **したがって現行方式の正しい呼び名は「LLMCompiler 型の plan-then-execute」**である(ReWOO は観測と推論の分離が主眼で、変数参照と DAG 実行を中心に据えていない)
- **ただし現行はトポロジカルな並列実行をしていない。**3 手を素朴に逐次実行している。原典が報告する 3.7× の短縮は**並列実行によるもの**なので、我々はその利得を取っていない(§3.8)
- **原典の失敗分析(Planner / Executor / 最終出力の内訳)は本文を取得できず未確認**(§5)。ただし「Planner が誤った変数 ID を出すと実行グラフが壊れる」という失敗の型自体は、方式から論理的に導かれる。現行の P4 は「前方参照であること・解決できること」しか見ておらず、**型の整合と循環の検出が抜けている**(§3.4)

#### ✅ Web エージェントは plan-then-execute を既定にすべき(2026)

- Julien Piet, Annabella Chow, Yiwei Hou, Muxi Lyu, Sylvie Venuto, Jinhao Zhu, Raluca Ada Popa, David Wagner, *"Web Agents Should Adopt the Plan-Then-Execute Paradigm"*, arXiv:2605.14290(2026-05-14 投稿)。https://arxiv.org/abs/2605.14290
- 抄録原文(確認済み):
  > "**Instead, web agents should default to plan-then-execute: commit to a task-specific program before observing runtime web content, then execute it.**"
  > "while **80% can be completed with a purely programmatic plan, without any runtime LLM subroutine**."

**本件との関係。**動機はプロンプトインジェクション耐性(Web 固有)なので**そのままは移せない**が、「**実行前に型付きプログラムへ固定し、制御フローを実行時データに書き換えさせない**」という原則は本件の設計と一致する。[ADR-0008](../adr/0008-plan-then-execute.md) の追加根拠になる。

### 2.2 構造化抽出 — 「並び順」が効く【最重要】

#### ✅ フォーマット制約は推論を損なう。しかも原因はキーの順序

- Zhi Rui Tam, Cheng-Kuang Wu, Yi-Lin Tsai, Chieh-Yen Lin, Hung-yi Lee, Yun-Nung Chen, *"Let Me Speak Freely? A Study On The Impact Of Format Restrictions On Large Language Model Performance."*, **EMNLP 2024 Industry Track**. https://aclanthology.org/2024.emnlp-industry.91/
- 抄録原文(確認済み):
  > "**Surprisingly, we observe a significant decline in LLMs' reasoning abilities under format restrictions.**"
- **§4.1 本文(確認済み)** — ここが本件にとっての核心:
  > "Upon inspection, we found that **100% of GPT 3.5 Turbo JSON-mode responses placed the 'answer' key before the 'reason' key, resulting in zero-shot direct answering instead of zero-shot chain-of-thought reasoning.**"
- 数値(確認済み): GSM8K / gpt-4o-mini — **自然言語 94.57 に対し JSON-Schema 91.71**、JSON-Mode 86.95、FRI(JSON) 87.17。さらに GPT-3.5-Turbo では **Text 75.99 → JSON + schema 49.25**

**本件との関係 — 現行スキーマは、この論文が指摘している並びそのものである。**

```jsonc
// agent_planning_phase.md §3.1 の現行スキーマ（順序に注目）
{
  "intent": "...",              // ← 結論
  "plan": [...],                // ← 結論
  "profile_delta": {...},       // ← 根拠
  "constraints": [...],         // ← 根拠
  "score_adjustments": [...],
  "unmodeled": [...],
  "references": [...]           // ← 根拠（照応の解決）
}
```

guided decoding はスキーマの順にトークンを生成させるので、**モデルは「どの Tool を使うか」を、制約を抽出し照応を解く前に決めることになる。**これは論文の "answer before reason" と同じ構造である。

**特に `references`(照応の解決)が最後にあるのは危うい。**「2 番目のやつを外して」の `plan` を、**どれが「2 番目のやつ」かを決める前に**書かせている。

> **注意 — [agent_planning_phase.md §3.2](agent_planning_phase.md) の現行の記述は、この証拠に照らして書き過ぎである。**
> 同節は JSONSchemaBench を引いて「制約付きデコーディングが精度を落とさないことを報告している(よく言われる 10〜15% 劣化を否定)」と書いているが、本論文は逆向きの結果を報告している。
> **両者は矛盾していない**と読むのが妥当である — **劣化の原因は「制約を課すこと」そのものではなく「課したスキーマの形」**(順序・CoT の余地のなさ)にある。つまり対処は「guided decoding をやめる」ではなく「**スキーマを直す**」である。§3.1 の提案はこの読みに基づく。

#### ✅ スキーマは静的な契約ではなく、最適化の対象

- Anubhav Shrimal, Aryan Jain, Soumyajit Chowdhury, Promod Yenigalla, *"PARSE: LLM Driven Schema Optimization for Reliable Entity Extraction"*, **EMNLP 2025 Industry Track**. https://aclanthology.org/2025.emnlp-industry.184/
- 数値(確認済み): **SWDE で最大 64.7% の抽出精度改善**、**初回リトライで抽出エラー 92% 減**
- 構成: ARCHITECT(スキーマ最適化)/ RELAY(コード生成)/ SCOPE(reflection つき抽出)

**本件との関係。**「JSON Schema を人間向けのデータ契約のまま LLM に渡さず、**説明の追加・構造の平坦化・フィールド間関係の明示**で LLM 向けに作り直す」という発想が本件に効く。**現行スキーマにはフィールドの説明文がほぼない**(型と enum だけ)。
なお **reflection(自己反省ループ)は採らない** — 常時実行すると 1 ターンの LLM 呼び出し予算を壊す。

### 2.3 サブエージェントの是非 — 分けないほうがよい側に証拠が集まった

#### ✅ マルチエージェントが効く条件・効かない条件

- Jeremy Hadfield ほか, *"How we built our multi-agent research system"*, Anthropic Engineering Blog, 2025. https://www.anthropic.com/engineering/multi-agent-research-system
- 原文(確認済み):
  > "We've found that **multi-agent systems excel at valuable tasks that involve heavy parallelization, information that exceeds single context windows, and interfacing with numerous complex tools.**"
  > "**some domains that require all agents to share the same context or involve many dependencies between agents are not a good fit for multi-agent systems today.**"
- 数値(確認済み): 内部評価で単一エージェント比 **90.2%** 改善。ただし **エージェントはチャットの約 4 倍、マルチエージェントは約 15 倍のトークンを使う**

**本件は「効く条件」の 3 つをどれも満たさない**(Tool は 5 個、1 ターン最大 3 手、43 POI)。一方で「**共有コンテキストと依存関係が多い**」という**効かない条件には当てはまる**。

#### ✅ 逐次計画タスクでは、むしろ悪化する

- Yubin Kim ほか, *"Towards a Science of Scaling Agent Systems"*, arXiv:2512.08296(2025-12 投稿 / 2026-04 改訂)。https://arxiv.org/abs/2512.08296
- 原文(確認済み):
  > "**Agent effectiveness depends on alignment between coordination and task structure, and that mismatched coordination degrades the performance.**"
- 数値(確認済み): 260 構成 × 6 ベンチマーク × 3 モデル系列。単一エージェント比で **分解可能な金融推論は +80.8%、逐次計画は −70.0%**。最適構成をホールドアウトの 87% で予測

**本件の 1 ターンは典型的な「逐次計画」**である(推薦 → その結果を旅程へ)。**−70.0% 側に該当する。**

#### ⚠️ マルチエージェントの失敗率

- Mert Cemri ほか, *"Why Do Multi-Agent LLM Systems Fail?"* (MAST), arXiv:2503.13657(2025-03 投稿 / 2025-10 改訂)。https://arxiv.org/abs/2503.13657
- 抄録で確認: **14 失敗モード / 3 分類**、**7 フレームワーク**、**1,600+ trace**、annotator 間 **κ = 0.88**
- 本文で確認(抄録には無い): "Our empirical analysis reveals **41% to 86.7% failure rate** on 7 state-of-the-art (SOTA) open-source MAS"

**結論: [agent_planning_phase.md §17](agent_planning_phase.md) の「サブエージェントを切り出さない」は維持する。**根拠が推測から実証に変わった。

### 2.4 我々のモデル固有のリスク

#### ✅ Gemma 4 + 構造化出力で反復ループ

- vLLM issue #40080, *"Gemma 4 (31B / 26B-A4B) generates infinite repetition loops, especially with structured output"*(2026-04-17 起票、**現在は closed**)。https://github.com/vllm-project/vllm/issues/40080
- 原文(確認済み):
  > "**The model generates a valid prefix, then enters a degenerate loop repeating a phrase with minor variations indefinitely until `max_tokens` is hit.**"
  > "The issue occurs **significantly more frequently when structured output (JSON schema / grammar constraints) is enabled.**"
- 対象は `google/gemma-4-31B-it` / `google/gemma-4-26B-A4B-it`。BF16 でも量子化でも観測。xgrammar が有効トークンを絞るため EOS を出せずループを抜けられない、と説明されている

**本件との関係。**我々は **`google/gemma-4-31B-it-qat-w4a16-ct` で全ターン guided decoding を使う。**モデル系列が一致するので無視できない。
**closed になっているが、どのバージョンでどう解決したかは未確認。**発生率の定量報告もない(単一 issue であり査読された測定ではない)。したがって「起きる前提で防御する」のが妥当である(§3.5)。

### 2.5 その他(要点のみ)

以下は Codex の報告に含まれるが、**Claude による一次資料検証は行っていない。**設計を変える根拠には使わず、**実装時に確認する項目**として記録する。

- 制約付きデコーディングの効果は**モデル依存**(base と instruction-tuned で逆向き)という報告(RANLP 2025)
- vLLM の **Automatic Prefix Caching は prefill だけを短縮する**(decode は短縮しない)
- **Lost in the Middle**(TACL 2024): 重要情報を文脈の中間に置くと性能が落ちる U 字傾向
- **RULER**(COLM 2024): 公称コンテキスト長は実効的な利用能力を保証しない
- プロンプト自動最適化(MIPRO / GEPA / AutoPDL)、self-consistency、CRANE、AgentSpec、MCP のエラー設計、OpenTelemetry GenAI semantic conventions(status は Development)

---

## 3. 現行設計への適用

### 3.1 【順位 1】`understand` のフィールド順を入れ替える

**変更案** — 根拠を先に、結論を後に:

```jsonc
{
  "references":        [...],   // ① まず「どれのことか」を確定する
  "profile_delta":     {...},   // ② 発話から読み取れた選好
  "constraints":       [...],   // ③ 述語に写せた要望
  "score_adjustments": [...],   // ④ POI 単位の補正
  "selection_hints":   [...],   // ⑤ 経路 3（understand_node.md §11-1 の提案）
  "unmodeled":         [...],   // ⑥ 写せなかったもの
  "intent":            "...",   // ⑦ ここまでを踏まえた結論
  "plan":              [...]    // ⑧ 結論（Tool の列）
}
```

- **効果の見込み**: 大。特に `references` を先頭に持ってくることで、**照応を解いてから `plan` を書く**順序が保証される(現行は逆)
- **コスト**: 低。スキーマ定義の並び替えだけで、planner・executor・Tool は一切変わらない
- **注意**: これは**仮説である。**§3.3 の評価集合がないと効果を確認できない。「理屈は通っているが未実証」の状態で入れることになる

### 3.2 【順位 2】スキーマを LLM 向けに作り直す

- 各フィールドに**説明文**を付ける(現行は型と enum のみ)
- **入れ子を浅くする**(`plan[].args` の中に Tool ごとに違う構造が入るのが最も深い。ここは §18.1 の「制約をターン全体に出す」で既に一段浅くなる)
- **相互排他と既定値を明示する**(例: `revert` op は他の op と併用不可 → スキーマで表現できるならする)
- 正例と**境界例**を few-shot として置く

### 3.3 【順位 3】抽出の固定評価集合を作る

**3.1 と 3.2 は「測れなければ入れる意味がない」。**[recommendation_planning.md §7](recommendation_planning.md) の層 1 テストを、抽出品質まで拡張する。

- 日本語発話を **50〜100 件**、種別ごとに用意する: 単純な選好 / 複合要求 / 照応(序数・別名) / 表現できない要望 / 曖昧
- 測る指標: **述語 F1** / 引数の値精度 / **`handling` の混同行列** / `plan` 妥当率 / 照応の正解率
- **複数 seed で回す**(1 回の結果で判断しない)
- これは**シナリオ台本(層 2)とは別物**。台本は回帰検知、こちらは変更の効果測定

### 3.4 【順位 4】`$N` の検証を強化する(P4 の拡張)

現行 P4 は「前方参照のみ・解決可能」だけを見ている。**LLMCompiler 型では、ここが実行グラフの健全性そのもの**なので足す:

| 追加する検査 | 何を防ぐか |
| --- | --- |
| **型の整合** | `$1.itinerary` を `targets`(`spot_id` の列)に渡すような取り違え |
| **循環の検出** | 前方参照のみの規則があるので理論上は起きないが、**規則が破れたときに黙って壊れない**ようにする |
| **Tool 入出力の互換性** | 手 1 が `spot_ids` を返さない Tool なのに `$1.spot_ids` を参照している |
| **失敗コードの固定** | どの検査で落ちたかを `rejected_steps` に残す(プロンプト改善のシグナル) |

### 3.5 【順位 5】反復ループを縮退表に入れる

[agent_planning_phase.md §9](agent_planning_phase.md) の表に行が足りない。

| 障害 | 縮退 | ユーザーへの表示 |
| --- | --- | --- |
| **LLM が反復ループに入る**(`max_tokens` まで止まらない) | **`max_tokens` を出力想定の 1.5 倍程度に設定 + ウォールクロックのタイムアウト + 同一 n-gram の反復検知で打ち切り。**`understand` なら再試行 1 回に落とす | 既存の「うまく理解できませんでした」に合流 |

`core/llm.py` に**反復検知を一元実装する**([20_architecture.md §7](../20_architecture.md) の「モデル依存の後処理は `core/llm.py` に一元化」に従う)。

### 3.6 【順位 6】固定プレフィックスを先頭に集める

[agent_planning_phase.md §7](agent_planning_phase.md) のコンテキスト予算は**内訳しか決めておらず、並び順を決めていない。**次の順に固定する:

```
① システムプロンプト + 出力スキーマ + Tool 説明   ← 毎ターン byte 単位で同一（prefix cache が効く）
② タグ語彙                                        ← ほぼ不変
③ プロファイル・現在の旅程                        ← ターンごとに変わる
④ 直近の会話                                      ← 最も変わる
⑤ 参照可能な spot_id の語彙
⑥ ユーザー発話                                    ← 末尾（"lost in the middle" を避ける）
```

prefix cache(prefill 短縮)と "lost in the middle"(重要情報を中間に置かない)の**両方に同時に効く**。コストはほぼゼロ。

### 3.7 【順位 7】検証失敗時に限った再計画【要判断】

現行は「Tool が失敗したら残りを中止」だけで、**再計画をしない。**LLMCompiler も再計画を持つ構成を評価している。

- **入れるなら厳しく縛る**: 再計画は**1 ターン 1 回まで**、**回復可能な失敗のみ**(参照の解決失敗・事前条件の不成立)、**Tool の実行失敗では再計画しない**(副作用が読めないため)
- **グラフが変わる**(§15.3 に `act → validate_plan` の辺が増え、巡回が 2 か所になる)
- LLM 呼び出しが 1 回増えうるので、NFR-3 とのトレードオフになる

### 3.8 【順位 8】独立した手の限定的な並列実行【要判断】

原典の 3.7× は並列実行によるものだが、**本件でそのまま得られるとは限らない。**

- **並列にしてよいもの**: Tool の I/O(OSRM 呼び出し、DB クエリ、知識検索)。**これは素直に効く**
- **慎重に扱うもの**: **LLM 呼び出しの並列化。**単一 GPU では総スループットは上がっても個々の TTFT が悪化しうる(§2.5)。しかも本件の GPU は**他プロセスと共有**している
- **現実的な範囲**: 1 ターン最大 3 手で、そのうち独立な組み合わせは限られる(`recommend` + `search_knowledge` 程度)。**得られる短縮は原典の想定より小さい**

**判断: Tool の I/O 並列化は入れる。LLM 呼び出しの並列化は、実測してから決める。**

---

## 4. 採らないもの(理由つき)

| 採らないもの | 理由 |
| --- | --- |
| **全面的な ReAct 自律ループ** | [§1.1 案 C](agent_planning_phase.md) の判断を覆す証拠は出なかった。むしろ 2026 年の研究が plan-then-execute を推している(§2.1) |
| **サブエージェントの切り出し** | §2.3。逐次依存タスクでは **−70.0%** の報告 |
| **常時 self-consistency(複数サンプリング)** | 共有 GPU を大きく消費する。検証失敗時の限定適用にとどめる |
| **常時 LLM critic / reflection** | 型・参照・DSL 規則で判定できるものはコードで足りる([§17 案 4](agent_planning_phase.md) の判断を維持) |
| **Planner の追加学習(Plan-and-Act 型)** | GPU が共有で学習不可能([recommendation_planning.md §1](recommendation_planning.md)) |
| **投機的デコーディング** | 出力が短い構造化 JSON では decode 短縮の余地が小さい。運用の複雑さに見合わない |
| **評価集合を作る前のプロンプト自動最適化(MIPRO / GEPA)** | 最適化対象が不安定なまま回すと過適合する。§3.3 が先 |
| **partial JSON を使った Tool の早期実行** | `plan` 全体と検証が確定する前に副作用を起こす。表示用途に限る |
| **コンテキストが逼迫する前の compaction** | 現行の見積もりは `understand` 約 4,500 / `respond` 約 7,000 トークンで、16K に対して余裕がある |

---

## 5. 確認できなかったこと

**Codex が報告したが、Claude が一次資料で確認できなかったもの。**本文の根拠には使っていない。

| 内容 | 状況 |
| --- | --- |
| LLMCompiler の失敗内訳(Planner 8% / Executor 64% / 最終出力 28%) | **論文本文を取得できず**(arXiv HTML・PMLR PDF とも 404)。§3.4 はこの数値に依存しない形で書いた |
| PARSE の内訳(スキーマ変更の 55% が構造再編 / 34% が説明強化 / reflection で +10.16 秒) | **抄録に記載なし。**本文未確認 |
| §2.5 に挙げた各項目(RANLP 2025 / APC / Lost in the Middle / RULER / MIPRO / GEPA / CRANE / AgentSpec / MCP / OTel ほか) | **Codex の報告のみ。**Claude による検証は未実施 |

さらに、**そもそも一次資料が存在しなかった**とされるもの(Codex の報告による):

- 現行の Gemma 4 量子化モデル + vLLM で、xgrammar / guidance などを同一条件比較した結果
- 日本語の開放的発話から DSL を抽出する際の、フィールド数・入れ子深度・enum 数それぞれの効果
- 現行プロンプトでの prefix cache ヒット率と TTFT 短縮値
- `handling` のような「LLM に表現可能性を自己申告させる」方式と完全に一致する先行研究

**これらは推測で埋めず、実装後のローカル計測に回す。**

---

## 6. 決着(2026-07-31)

6 論点すべてを決定し、[agent_planning_phase.md](agent_planning_phase.md) に反映した。同文書 §23(決着の記録)と §24(改訂履歴と採らなかったもの)が正。

| # | 論点 | 決定 | 反映先 |
| --- | --- | --- | --- |
| 1 | フィールド順の入れ替え | **採用。**理屈は通っているが本件での実証はないので、**§24.4 で「実装後に一度測る」対象に入れた** | §3.1 / §3.1.1 |
| 2 | §3.2 の記述の修正 | **修正した。**両論を併記し、**対処が「guided decoding をやめる」ではなく「スキーマを直す」**であることを明示 | §3.2 |
| 3 | 抽出の評価集合 | **作る。ただし別途 authoring しない** — シナリオ台本(層 2)から派生させ、紛らわしい照応と表現できない要望を 10 件ほど手で足す | §24.4 |
| 4 | 検証失敗時の再計画 | **採らない。**グラフの巡回が 1 か所という性質を保つ。加えて**対話システムではユーザーの次の発話が事実上の再計画**であり、機械的な再計画は人間のループと重複する | §24.2 |
| 5 | Tool I/O の並列化 | **Tool の内側だけ採る**(OSRM の leg 取得など)。**手の並列実行はしない** — 最大 3 手で独立な組み合わせが乏しく、executor の失敗処理が複雑になる代償に見合わない | §24.2 |
| 6 | 未検証項目の追加調査 | **やらない。**§5 の未確認項目はいずれも**設計を決める材料ではなく、実装後にローカルで測る項目**である。2 巡目の調査より計測基盤を作るほうが早い | §24.4 |

**あわせて、この調査から直接は出ていないが、改訂の過程で決めたもの**: `constraints` の永続化に **`constraints_remove` を対にした**(永続化する以上、ユーザーが取り消せないと制約が旅程に張り付くため)。詳細は [agent_planning_phase.md §4.4](agent_planning_phase.md)。
