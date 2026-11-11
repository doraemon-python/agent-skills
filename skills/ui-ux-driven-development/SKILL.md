---
name: ui-ux-driven-development
description: UI/UX駆動開発（UI/UX Driven Development）で個人〜少人数向けのアプリを開発するためのワークフロー。詳細な設計書を先に固めるのではなく、モックデータで動くUIを先に作ってユーザーに触ってもらい、その実感をもとにロジック仕様とバックエンドを決める。技術的な判断の理由は ADR として docs/decisions/ に記録する。新しいアプリを作り始めるとき、既存アプリに機能追加・UI変更・バグ修正をするとき、「UIから作りたい」「まず画面を見たい」「プロトタイプから始めたい」と言われたとき、docs/progress.md があるプロジェクトで作業を再開するときは、明示的に指定されていなくても必ずこのスキルを使う。
license: MIT
metadata:
  version: "0.1.0"
---

# UI/UX Driven Development

## このスキルの考え方

仕様を文章で先に固める開発（Spec-Driven Development など）では、ユーザーはアプリの実体を見ないまま設計を承認することになり、曖昧な判断が後で手戻りになりやすい。このスキルでは、判断材料になる「触れるもの」を先に作り、ユーザーにはそれを見て判断してもらう。

前提と原則は次のとおり。

1. **対象は個人開発〜少人数開発。** 重いプロセスより、作成→修正のループを速く回すことを優先する。
2. **コードをSSOT（信頼できる唯一の情報源）とする。** 設計ドキュメントは作業中だけ使う一時ドキュメントとし、役目を終えたら削除する。ただし「なぜそう決めたか」という判断と思想は永続ドキュメントに残す。技術的な判断は ADR として残す（「判断理由の記録（ADR）」を参照）。
3. **提案は惜しまず、承認は少なく。** 迷ったら推測で進めずユーザーに聞く。一方で、ユーザーに成果物を確認してもらう「承認」は5回程度に絞り、その前にAIが自分でチェックリストを使って品質を確認する。ユーザーの時間は「判断」に使ってもらい、「間違い探し」には使わせない。
4. **UIで分かったことをロジックとバックエンドに流す。** UIを作る過程で見つかったルール（並び順、表示条件、バリデーションなど）を記録し、後のステップで確定させる。

---

## 全体フローと読むファイル

各Stepの手順とチェックリストは `references/` に分けてある。**作業中のStepのファイルだけを読む。** Step0 はこのファイルにある。

| Step | 内容 | 提案 | 承認 | 主な成果物 | 手順 |
|---|---|---|---|---|---|
| 0 | 開始・再開 | — | — | `docs/progress.md` | このファイルの「Step 0」 |
| 1 | ユーザー意図の理解 | P1 | （Aに統合） | `docs/intent.md` | [step1-intent.md](references/step1-intent.md) |
| 2 | 技術仕様の決定と初期化 | P2 | **A** | 依存関係ファイル、初期化済みプロジェクト、ADR | [step2-tech-stack.md](references/step2-tech-stack.md) |
| 3 | 画面構成の設計 | P3 | **B** | 画面スケルトン、`docs/_tmp/screens.md` | [step3-screens.md](references/step3-screens.md) |
| 4 | UIの作成 | P4, P5 | **C** | モックで動くUI、カタログ（Storybook等） | [step4-ui.md](references/step4-ui.md) |
| 5 | 内部リファクタリング | — | （報告のみ） | 整理されたコンポーネント構造 | [step5-refactoring.md](references/step5-refactoring.md) |
| 6 | ロジック仕様の決定 | P6 | （Dに統合） | `docs/logic-spec.md` | [step6-logic-spec.md](references/step6-logic-spec.md) |
| 7 | バックエンド設計 | P7 | **D** | `docs/_tmp/backend-design.md`、ADR | [step7-backend-design.md](references/step7-backend-design.md) |
| 8 | 実装・テスト・結合 | — | （報告のみ） | バックエンド、テスト、結合済みアプリ | [step8-implementation.md](references/step8-implementation.md) |
| 9 | 最終確認 | — | **E** | 完成したアプリ | [step9-final-check.md](references/step9-final-check.md) |
| — | 追加開発（機能追加・UI変更・バグ修正） | — | 規模による | — | [additional-development.md](references/additional-development.md) |
| — | ADR を読む・書くとき（Step2・7・8、追加開発） | — | — | `docs/decisions/` | [adr.md](references/adr.md) |

テンプレートは `assets/` にある。該当するStepで、コピーして使う。

| テンプレート | 使う場面 |
|---|---|
| [approval-request.md](assets/approval-request.md) | すべての【承認】の依頼 |
| [proposal.md](assets/proposal.md) | すべての【提案】 |
| [progress.md](assets/progress.md) | Step0 で `docs/progress.md` を作るとき |
| [open-questions.md](assets/open-questions.md) | Step0 で `docs/open-questions.md` を作るとき |
| [intent.md](assets/intent.md) | Step1 で `docs/intent.md` を作るとき |
| [ui-principles.md](assets/ui-principles.md) | Step4 で `docs/ui-principles.md` を作るとき |
| [logic-spec.md](assets/logic-spec.md) | Step4 で `docs/logic-spec.md` を作るとき |
| [decisions-index.md](assets/decisions-index.md) | 最初の ADR を書くときに `docs/decisions/README.md` を作るとき |

---

## 【提案】と【承認】の定義

このスキルでは、ユーザーとのやり取りを2種類に分ける。各Stepの手順中の **【提案Pn】** と **【承認X】** がその発生箇所である。

### 【提案】＝ 作る前に方向性を聞く

目的は、推測で進めないこと。ドメイン知識の不明点、複数の妥当な選択肢がある判断、ユーザーの意図の確認などで使う。**提案は渋らない。** 迷ったら出す。

提案のルール（この順に行う）:

1. 聞きたいことを一度にまとめる。1問ずつ往復しない。
2. できる限り選択式にし、推奨案に「推奨」と理由を付ける。ユーザーが選ぶだけで済むようにする。選択肢には「その他（自由記述）」を加える。ADR の対象になる判断は、`references/adr.md` の「3. 選択肢を示す」に従って比較する。
3. ユーザーがすでに答えている事項や、資料から確実に読み取れる事項は聞かない。
4. 回答が必要な提案を出したら、そのターンはそこで終えて回答を待つ。
5. 作業中に気づいた改善案やスコープ外のアイデアは、作業を止めずに `docs/open-questions.md` に書き、次の提案か承認のタイミングでまとめて伝える。勝手に実装はしない。
6. 回答は該当ドキュメントに反映する。

形式は [assets/proposal.md](assets/proposal.md) に従う。ユーザーの環境に選択式の入力UIがあれば、それを使って提示してよい。

### 【承認】＝ 作った後に成果物を確認してもらう

目的は、成果物がユーザーの意図と一致しているかを、実物を見て判断してもらうこと。承認は次のステップに進むためのゲートである。

承認のルール（この順に行う）:

1. 該当ステップのセルフチェックリストを**全項目**実行する。
2. 未達の項目は直す。どうしても直せない項目は、理由と対応方針を承認依頼に明記する。
3. [assets/approval-request.md](assets/approval-request.md) の形式で依頼する。確認方法（起動コマンド、見るべき画面、触るべき操作）を具体的に書き、ユーザーが迷わず実物に触れるようにする。記録する ADR があれば「記録するADR（案）」に書く。
4. 承認を依頼したら、そのターンはそこで終える。返答を待たずに次のステップへ進まない。
5. 明示的な承認（「承認」「OK、進めて」など）だけを承認とみなす。「いいね」「なるほど」のような反応だけの場合は、承認してよいかを確認する。
6. 修正指示を受けたら、修正 → 該当チェックリストの再実行 → 変更点だけを示して再依頼、を繰り返す。
7. 承認を得たら `docs/progress.md` に日付と内容を記録する。

### 承認ゲート一覧

| ゲート | タイミング | ユーザーに確認してもらうもの |
|---|---|---|
| 【承認A】 | Step2 の終わり（初期化の前） | ユーザー意図のまとめ ＋ 技術仕様 ＋ 記録するADR |
| 【承認B】 | Step3 の終わり | クリックで遷移できる画面スケルトン |
| 【承認C】 | Step4 の終わり | モックデータで動くUI一式 |
| 【承認D】 | Step7 の終わり | ロジック仕様 ＋ バックエンド設計 ＋ 記録するADR |
| 【承認E】 | Step9 の終わり | 本物のバックエンドにつながった完成アプリ |

Step1、Step5、Step6、Step8 には承認ゲートがない。Step1 の成果は【承認A】、Step6 の成果は【承認D】でまとめて確認してもらう。Step5 と Step8 はユーザーに見える挙動を変えないか、すでに承認された仕様の実装なので、完了報告だけにする。

---

## ドキュメント構成

```
docs/
├── progress.md           # 永続: 現在のStep・承認履歴・次のアクション（作業開始時に必ず最初に読む）
├── intent.md             # 永続: ユーザー意図、用語集、非機能要件
├── ui-principles.md      # 永続: 修正指示から得たUI/UX思想、不採用案とその理由
├── logic-spec.md         # 永続: ロジック仕様（ルールID、なぜ、対応するテスト）
├── open-questions.md     # 永続: 未決事項、後で決める事項、気づいた改善案
├── decisions/            # 永続: 技術的な判断の ADR
│   ├── README.md         #   ADR の索引（frontmatter から作る一覧表）
│   └── NNNN-<slug>.md    #   1ファイル = 1つの決定
└── _tmp/                 # 一時: Stepの完了時に削除する
    ├── screens.md        # Step3〜4: 画面一覧、各画面の目的、遷移図
    └── backend-design.md # Step7〜8: バックエンド設計
```

ドキュメントの扱いのルール:

1. 依存関係は `package.json` や `requirements.txt` などをSSOTとし、技術スタックを説明するドキュメントは書かない。技術を選んだ理由は ADR に書く。
2. `_tmp/` のファイルを削除する前に、残す価値のある判断（なぜそうしたか）を、下の「判断の記録先」に従って永続ドキュメントへ移す。
3. 永続ドキュメントは簡潔に保つ。コードやテストを読めば分かることは書かず、「なぜ」と「コードから読み取れないこと」だけを書く。

---

## 判断理由の記録（ADR）

技術的な判断の理由は、プロジェクトのルールの有無にかかわらず、**常に [references/adr.md](references/adr.md) に従って ADR として `docs/decisions/` に記録する。** `references/adr.md` は adr スキルと同じ規約なので、adr スキルが別に入っていても記録の結果は変わらない。

### 判断の記録先

| 判断の種類 | 記録先 |
|---|---|
| 技術・アーキテクチャの判断（言語、フレームワーク、ライブラリ、認証方式、レンダリング方式、デプロイ先、DB・ORM、データモデル、データアクセス層の契約、層の分け方、インフラ、開発プロセス）のうち、**後から変えにくいか、理由がコードから読み取れないもの** | ADR（`docs/decisions/`） |
| 上の基準を満たさない小さな技術判断 | ドキュメントに残さず、コミットメッセージかPRの説明に書く |
| UI/UXの思想、デザインの不採用案 | `docs/ui-principles.md` |
| ロジックのルールとその理由 | `docs/logic-spec.md` |
| まだ決めていないこと | `docs/open-questions.md` |

### ワークフローの中での ADR の扱い

1. **提案の前に既存の ADR を読む。** 技術的な判断を提案する前に `docs/decisions/` の有効な ADR を確認し、矛盾する案を黙って出さない。変えるなら置き換え（supersede）として明示する。
2. **ADR の確認は承認に含める。** ADR を書く前に必要な「決定内容の確認」は、別の往復にせず、【承認A】【承認D】の依頼の「記録するADR（案）」で行う。承認ゲートのない場面（Step8、追加開発、バグ修正）では、その判断を聞く【提案】の回答で確認する。
3. **承認・回答を得るまで `accepted` で書かない。** ユーザーが方向性だけ示して決めきっていない場合は `proposed` で書く。
4. **ユーザーが同意した理由だけを書く。** 承認依頼や提案で示し、ユーザーが同意した理由か、ユーザー自身が述べた理由だけを記録する。
5. ADR を書く主な場面は、Step2（承認A後）、Step7（承認D後）、Step8（実装中の設計変更）、追加開発（ライブラリの導入、過去の決定の変更）。各Stepの手順に具体的に書いてある。

---

## Step 0. 開始・再開

セッションをまたいで作業が続くため、毎回ここから始める。

1. `docs/progress.md` があるか確認する。
2. ある場合は読み、「現在のStep」と「次のアクション」から作業を再開する。承認待ちの状態だった場合は、ユーザーの返答が承認か修正指示かを判断する。
3. プロジェクトのルールファイル（`CLAUDE.md`、`AGENTS.md`、`README` など）を読み、コーディング規約などがあれば把握する。
4. `docs/decisions/README.md`（ADR の索引）があれば読み、有効な決定を把握する。これから触る範囲に関係する ADR の `revisit_if`（見直す条件）が満たされていれば、ユーザーに伝える。
5. 依頼が新規開発か追加開発かを判断する。追加開発なら [references/additional-development.md](references/additional-development.md) へ進む。
6. 新規開発で `docs/` がない場合は、`docs/progress.md` と `docs/open-questions.md` をテンプレートから作成し、Step1 へ進む。`docs/decisions/` は最初の ADR を書くときに作る。
7. 進むStepのファイルを「全体フローと読むファイル」の表から開き、その手順に従う。
