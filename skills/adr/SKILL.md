---
name: adr
description: Help the user make an architectural or technical decision through conversation, then record it as an ADR in docs/decisions/. Use this skill whenever the user is weighing options for a design, technology, library, data model, API contract, infrastructure, or process choice; asks "which should we use", "should we change X", "why did we do Y"; wants to reverse or revisit an earlier decision; or says anything like "ADRにして", "決定を記録して", "record this decision". Also use it proactively when a conversation has clearly converged on a decision that is costly to reverse or not obvious from the code, and offer to record it.
---

# ADR: decide in conversation, record faithfully

This skill has two jobs, in this order:

1. **Inform the decision.** Give the user what they need to decide well, grounded in this repository, not in generic best practice.
2. **Record the decision.** Write it to `docs/decisions/` exactly as it was decided, in the format below.

The human decides. You research, frame, compare and recommend, but you never write an ADR as `accepted` until the user has explicitly chosen.

## Phase 1: Ground yourself before saying anything substantive

Earlier decisions and the current code are the real constraints. Read them first, because a recommendation that contradicts an accepted ADR without noticing it is worse than no recommendation.

- Find related decisions. Search filenames and frontmatter rather than reading every file:
  - `ls docs/decisions/`
  - `rg -l "status: accepted" docs/decisions/` to restrict to decisions currently in force
  - `rg -l "tags:.*<topic>" docs/decisions/` and `rg -l "code:.*<path>" docs/decisions/`
  - `rg "ADR-[0-9]{4}" <relevant source paths>` to find decisions anchored in the code you are about to touch
- Read the relevant code, config and tests. Prefer what the code actually does over what any document says it does.
- Read `docs/domain/` if the decision touches business rules.
- Check `revisit_if` on related accepted ADRs. If a condition has been met, say so: that is often the real reason the conversation is happening.

If an accepted ADR already covers the question, tell the user and quote its decision before discussing anything new. The outcome may be "keep it", or a new ADR that supersedes it.

## Phase 2: Frame the question

State back, briefly:

- The question being decided, as one sentence.
- Constraints you found (existing ADRs, code, domain rules) and constraints the user stated.
- What is out of scope.

Then check that it deserves an ADR. The bar is: **costly to reverse, or the reason will not be obvious from the code later.** If it fails both, say so and suggest recording it in the PR description or commit message instead. If one conversation contains several independent decisions, split them; one ADR records one decision.

## Phase 3: Inform

Present 2 to 4 real options. Include "keep the status quo" when it is a genuine option. Do not pad the list with options nobody would pick.

For each option, cover what matters for this repository: fit with existing decisions and code, cost to implement now, cost to reverse later, operational and maintenance burden, and risks. Keep facts, estimates and guesses visibly separate. When something can be settled by evidence (a quick spike, a benchmark, reading a library's source, checking a version), say what would settle it and offer to do it rather than guessing.

Then give your recommendation and the reasoning, including what would change your mind. Be direct; the user can disagree.

Answer follow-up questions in conversation. Do not start writing the ADR while the user is still deciding.

## Phase 4: Confirm the decision

Once the user has chosen, restate in one short paragraph what you are about to record: the decision, the main reason, the rejected options with one-line reasons, and the revisit condition. Ask the user to confirm or correct it.

Record only reasons the user agreed with or stated. Do not add rationale that was not part of the conversation; an ADR that claims reasons nobody held misleads every future reader. If the user wants to record a direction without committing, use `status: proposed`.

## Phase 5: Record

### Numbering and filename

- Next id = highest existing id + 1, zero-padded to 4 digits. Never reuse or renumber.
- Filename: `docs/decisions/NNNN-english-kebab-slug.md`. Keep the slug in English ASCII and include the words someone would search for (`0087-replace-postgres-job-queue-with-sqs.md`, not `0087-queue.md`). The filename is the cheapest search index there is.
- Get the date from the environment (`date +%F`), never from memory.

### Language

- Write the ADR body in **Japanese**.
- Keep frontmatter keys in English. Write `tags` in lowercase English, and reuse tags that already exist in other ADRs before inventing new ones (`rg -h "^tags:" docs/decisions/ | sort | uniq -c`), so the vocabulary stays searchable.
- `title` and `revisit_if` are in Japanese.

### Template

Use exactly this structure. Keep the whole ADR to roughly one screen; link to issues, PRs or benchmarks instead of pasting them.

```markdown
---
id: "0087"
title: ジョブキューをPostgresからSQSに移行する
status: accepted
date: 2026-10-19
supersedes: ["0042"]
superseded_by: []
tags: [infra, queue, async]
code: [src/jobs/, infra/sqs.tf]
revisit_if: 1日あたりのジョブ数が1,000件未満に戻った場合
---

# 0087. ジョブキューをPostgresからSQSに移行する

## 背景

なぜ今この決定が必要になったか。前提となる事実、制約、関連するADR（ADR-0042）を簡潔に書く。

## 決定

何をするかを1〜3文で書く。「〜する」と言い切る。

## 検討した選択肢

### A. SQSに移行する（採用）

採用理由。

### B. Postgresのまま、ワーカーを水平分割する（不採用）

不採用の理由。

### C. 現状維持（不採用）

不採用の理由。

## 結果・影響

- 良くなること
- 悪くなること、受け入れたトレードオフ
- この決定に伴って必要な作業（移行、削除するコード、更新すべきドキュメント）
```

Field rules:

- `id` is a quoted string so YAML keeps the leading zeros.
- `status` is one of `proposed`, `accepted`, `superseded`, `deprecated`.
- `supersedes` and `superseded_by` list quoted ids; use `[]` when empty.
- `code` lists the paths where the decision takes effect. It is how someone editing those paths finds this ADR.
- `revisit_if` states the assumption that, if broken, should reopen the decision. Make it observable ("〜になった場合"), not vague ("状況が変わったら").

### Superseding and changing past decisions

Never rewrite the body of an accepted ADR. History is the point. When a decision changes:

1. Write a new ADR with `supersedes: ["NNNN"]` and explain in 背景 why the old decision no longer holds.
2. In the old ADR, change only `status: superseded` and `superseded_by: ["MMMM"]`.

For a decision that is simply abandoned with no replacement, set `status: deprecated` and add one dated line at the end of the body saying why. Fixing typos and broken links is fine at any time.

### Anchor the decision in code

Where the decision explains code that would otherwise look odd or tempting to "fix", propose a short comment referencing it, and add it once the user agrees:

```ts
// ADR-0087: 再試行はSQSの可視性タイムアウトに任せる。アプリ側でリトライしない
```

Do this sparingly: at the places a future reader would otherwise undo the decision, not on every file in `code`.

### Update the index

`docs/decisions/README.md` is an index derived from frontmatter. If the repository has a generator for it (check `AGENTS.md`, `Makefile`, `package.json` scripts), run it. Otherwise update the existing table in place, following its current format, and mention to the user that a generator would remove this manual step.

### Finish

Tell the user which files you created or changed, and suggest a commit message such as `docs(adr): 0087 ジョブキューをSQSに移行`. If the ADR's 結果・影響 lists follow-up work, offer to start it or turn it into issues.

## Things to avoid

- Writing an ADR before the user has decided, or as `accepted` when they only leaned toward an option.
- Describing the current structure of the code. ADRs record why, not what; the code is the source of truth for what.
- Recording decisions that are trivially reversible or obvious from the code.
- Copying long excerpts from conversations, documentation or benchmarks into the ADR. Summarize and link.
- Silently contradicting an accepted ADR. Either respect it or supersede it explicitly.
