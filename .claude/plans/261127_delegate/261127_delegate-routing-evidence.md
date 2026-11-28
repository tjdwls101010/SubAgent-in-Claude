I found about 40 user interventions about the worker engine. Most are about Codex quota, but a distinct minority show reasons the global rule ("codex by default for delegation and verification; claude when asked or codex is unavailable") doesn't cover. Cross-engine handoffs happened at least four times, always by passing the working tree or git state plus a short preamble, never the other engine's thread.

Conventions:
- Transcript evidence is `<project dir>/<session id prefix> L<line>` under `~/.claude/projects/`.
- Times are UTC as stored in transcripts (KST is +9h, so memory files sometimes show the next day).
- `~/.claude` sessions are the project dir `-Users-seongjin--claude`.
- Headless worker sessions (entrypoint `sdk-cli`) and the excluded projects are left out.
- Messages the user typed while the orchestrator was mid-turn are stored as `queued_command` attachments, and AskUserQuestion answers sit inside tool results ("Your questions have been answered"). My first pass missed both; they are included below.

## Headline findings

1. **Quota is the most common reason, and the user's tolerance for waiting changed.**
   - 2026-10-29 the user chose "그대로 기다리기" for Codex verification (Agentic-Invest 410dfeaf L14396, decision D10).
   - From November the user says not to wait: "코덱스가 초기화될 때까지 기다리지 말고 /claude를 사용해" (e089aa91 L2076).
   - Sometimes the user drops the review rather than substitute: "코덱스 한도가 찼으면, 굳이 하지 않을게." (-claude fb5f153a L1439).
2. **Analysis, translation and report writing are "Claude's own work"; delegating them to Codex was rejected twice.** The orchestrator admitted the global rule pushed it to delegate work it could have done itself. Two Obsidian memories now override the rule.
3. **Three repos prefer Claude for review with no stated reason**, and Codex was available at the time (Ultra-Search 11-07, motion-graphic 11-14). The orchestrator's guess (Codex can't render) is contradicted by the registry: Claude workers couldn't launch Chrome in the sandbox either.
4. **The user explicitly asked for case-by-case judgment instead of a fixed priority.** That wording already lives in `~/.claude/hooks/plan_mode_brief.md:11`: Codex is strong at doubting a plan's assumptions; a Claude worker inherits the Claude Code environment (skills, hooks, memory) and can actually run the plan against it; use both only for big or hard-to-reverse plans.
5. **Environment differences that matter are concrete:**
   - Codex has no Skill tool: a Codex run refused to implement because its brief required invoking `coding` via the Skill tool (Agentic SNS run `20261116-041913-run-da9e`).
   - Scheduling exists only in the claude skill.
   - Scenario runs that test a Claude skill must run on Claude, on the model that will use it.
   - Image generation goes through Codex.
   - Claude's `write` profile can't edit `.claude/skills/` (run `20261116-134251-r11-95d4` had to leave a patch file instead).
6. **The orchestrator's own assessment of Codex's value** (Agentic-Invest e089aa91 L6184, 2026-11-22): it could not say Codex was clearly worth it. Codex found 9 sentence fixes, but those sentences had been rewritten after the Claude review and no one had re-read them. 1 of Codex's 12 findings was wrong, and 1 contradicted the earlier Claude reviewer. Its conclusion: what helped was "마지막에 고친 문장을 독립적으로 한 번 더 대조하는 것", not the model family.

## 1. User messages that choose, correct or question the engine

### A. Codex quota exhausted (switch, don't wait, or skip)

**Plan-prompt template, 2026-10-28 to 11-05.** Pasted into about 10 sessions: "codex(gpt-6.1-terra;high;quota가 찼으면 sonnet을 사용해도 좋아)". Examples: Agentic-Invest 410dfeaf L16, Politics f8cd28b7 L16, Video 6ddb27d1 L13, Naver-News a83fb6d4 L13, docs-find 29869b21 L17.

**Same template from 2026-11-07 on.** It becomes "실측과 `codex`(gpt-6.1-terra)와 `claude`를 통해 검증도 꼼꼼히". Examples: Agentic-Brain fe1c77e9 L9, Discord 41642d62 L9, Video f2cc1981 L13. So the fallback became a co-equal option once the claude skill existed (first Claude run 2026-10-31).

| Project | Date | Message | Note |
|---|---|---|---|
| Agentic-Design 059e95c3 L9 | 10-31 | "지금 codex의 quota는 다 찼으니까 스킬 `claude`를 사용하면 될거 같아." | |
| -claude fb33ee4e L377 | 11-02 12:34 | "검증은 /claude로 하면 되." | No reason given, but Codex had hit its limit at 11-02 ~12:00 UTC (reset Nov 4 17:09), so quota is plausible. |
| CLI-Maker e4118889 L635 | 11-01 | "codex가 quota가 찼으니, /claude 를 사용해도 되." | |
| Video 6ddb27d1 L784 | 11-03 | "/claude 로 점검을 해. 코덱스는 지금 쿼터가 찼어." | |
| Video bd3bc079 L166 / L228 | 11-03 | "참고로 코덱스의 쿼터가 지금 찼어." / "…고갈 되었다는 뜻이야" | |
| Naver-News a83fb6d4 L415 | 11-03 | "codex의 쿼터가 다 찼으니까 /claude를 사용해" | |
| Video bd3bc079 L1739 (answer) | 11-03 | Chose "지금 릴리스" | Released after a Sonnet-only review instead of waiting for Codex. |
| Video 9403c114 L2288 (answer) | 11-14 | Approved PR A | Claude-only review; Codex quota was exhausted. |
| Agentic-Invest 410dfeaf L6723 | 10-28 | "지금 codex quota가 초기화되었다." | Orchestrator used it to re-judge Sonnet verdicts blind and run a fidelity check ("The post-holdout edits haven't been seen by a different model family yet"). |
| Agentic-Invest e089aa91 L2076 | 11-20 15:42 | "코덱스가 초기화될 때까지 기다리지 말고 /claude를 사용해" | Orchestrator had just said it would rerun the Codex draft review when the limit reset at 01:10 KST. |
| Agentic-Invest e089aa91 L5422 | 11-21 | "아마 코덱스의 한도가 조만간 다 찰 가능성이 커보인다." | Orchestrator promised per-chapter fallback to Claude workers. |
| Agentic-Invest e089aa91 L6156 | 11-22 02:02 | "코덱스 한도가 풀리려면 2시간도 넘게 남았는데, 꼭 코덱스를 사용해야 해? 클로드로만 하는거보다 코덱스도 함께 사용하는게 확실히 효용이 있어?" | Answered at L6184 (finding 6). |
| Agentic-Invest d8c9b016 L490 | 11-22 | "참고로 지금 코덱스 한도가 찼으니까 코덱스는 사용하지 않아도 되." | |
| -claude fb5f153a L1439 | 11-22 | "코덱스 한도가 찼으면, 굳이 하지 않을게." | A Codex review had been scheduled for 13:23. |
| Agentic SNS 5904a1ce L14557 / L14575 | 11-16 07:01 | "채점은 /claude로 하면 되. codex로 점검을 안해도 되." then "sonnet으로만 해도 되." | Codex limited since 11-16 04:42 UTC (resets Nov 18). The reply says it won't wait for Codex. |

### B. The task is analysis, judgment or authoring (Claude's own work)

**Obsidian Work 2e4ee9c1, 2026-11-08 (subtitle translation).**
- L390: "번역은 클로드가 하면 되는데", sent while Codex run `20261108-165850-ko4-ca75` was translating in parallel segments.
- L437: answered "전부 클로드가 새로 (추천)", discarding the Codex drafts.
- L666: "아까 왜 codex로 자막을 만드려고 했어? … 그냥 너가 그렇게 한거지?" The orchestrator replied that the global CLAUDE.md "위임 기본값은 codex" drove the choice of engine, "번역은 직접 할 수 있었으니 위임이 필요한 일도 아니었습니다".
- Memory: `-Users-seongjin---Obsidian/memory/video-subtitle-translation-by-claude.md`.

**Obsidian 851b67f2, 2026-11-19 (investment analysis).**
- L2054 "코덱스로는 뭘 하는거야?". Codex was writing per-ticker investment analyses.
- L2071 "분석은 클로드가 하는거지 코덱스가 하는게 아니야. 코덱스는 사용하지마."
- The orchestrator stopped COHR run `20261119-201927-cohr-201c`, discarded its changes with `git checkout`, and redid COHR and the already-merged LITE (PR #151) with Claude.
- L2154: "`claude`에게 `invest` 스킬을 로드하고 각 티커를 업데이트 하라고 할거면 병렬로 해도 되지 않나?" The worker needs the user's skill.
- Memory: `invest-analysis-by-claude.md`.

**Agentic-Invest e089aa91 L5382, 2026-11-21: "장마다 코덱스보다는 클로드에게 맡기는 건 어때?"** (book-chapter extraction).
- The orchestrator argued for Codex (L5410): it is the only other-family reader of the source; Claude's quota should be kept for judging, where conditions need the same judge; and the work had broken off four times, every time on the Claude limit.
- L5414: user "그래. 일단 코덱스로 가자."

### C. Prefer Claude for verification or review (a preference, not quota)

| Project | Date | Message | Codex quota at the time |
|---|---|---|---|
| Ultra-Search 95b89717 L24 / c7bf76ff L5 | 11-07 00:10 | "검증은 `codex`가 아닌 `claude`를 사용하면 좋겠어." | Available: no limit event between the Nov 4 reset and 11-13; 6 Codex runs completed that day. No reason recorded; the plan's line 345 says only "성진의 지시로". |
| motion-graphic be5cd0cd L1422 | 11-14 15:23 (11-15 KST) | "리뷰는 코덱스보다는 클로드로 하는게 나을거 같아." | Available: the 11-13 limit reset at Nov 14 04:01 KST, and PRs #20 and #21 had just merged on Codex review. |
| motion-graphic 58119189 L249 | 11-23 13:07 | "구현 과정에서 codex는 사용하지마. 필요하면 스킬 claude는 사용해도 되지만." | Codex was in use until ~14:50 UTC, so likely available. |
| motion-graphic 58119189 L3650 | 11-26 | "작업을 이어서 해. 코덱스는 사용하지 말고." | No limit event. |
| Agentic SNS 5904a1ce L5406 | 11-15 11:37 | "다음부터는 검증을 `codex`가 아닌 `claude`(sonnet)으로 해." | Ambiguous: Codex had hit its limit ~11:05 UTC, reset 11:16 UTC, and a Codex implementation run was active. The memory notes no reason was given. |

For motion-graphic, memory `review-with-claude-not-codex.md` records the orchestrator's guess: Codex can't see renders. The same memory then records that Claude `write` workers couldn't launch Chrome either.

Claude added rather than replacing Codex:
- docs-find 29869b21 L1308 (11-04): "/claude로도 검증을 해도 괜찮을거 같네."
- Politics 9ed60259 L91 (11-05): "필요하면 /claude 로 검증을 해도 좋아."
- Agentic-Invest 67774788 L2025 (11-27): "/claude 도 사용해도 되", after the orchestrator gave a full skill audit to Codex alone "제 판단에 묶이지 않도록".

### D. Cross-model verification or several perspectives (user-initiated)

- **Agentic-Invest 410dfeaf L7609 (10-29), answer "sonnet 작성, codex 검증 (추천)".** Question premise: same-family writer and checker share misreadings; "codex가 sonnet이 놓친 것을 잡았어".
- **2026-11-07, answer "두 레인 모두 (추천)"** in -claude 9859ef56 L203, e158c7ec L104 and Agentic-Brain fe1c77e9 L827. Lanes: Codex checks the plan against real code and docs; a Claude Sonnet worker in the implementer's seat lists where it would have to guess.
- **e158c7ec L361:** whether to add an early Codex pass → "클로드가 판단".
- **Explicit both-engine requests:**
  - Agentic-Invest 25a8d839 L232 (11-14): "/claude /codex 등을 포괄적으로 사용해 다양한 관점에서 점검"
  - SNS 4251a60e L17 (11-15): "`codex`와 `claude`까지 여러개 사용해가며 다양한 관점에서"
  - Agentic-Invest 1d5894b3 L17 (11-19): "/claude /codex와 다양한 관점에서 너가 스스로 토론"
- **15 implementation sessions open by invoking both `/codex` and `/claude`** (for example SNS 5904a1ce L37/L40, Video 030beed2 L34/L37, Politics 10235ace L33/L36).
- **-claude fb5f153a L537 (11-22), tool rejection:** "검증은 codex와 claude 모두를 사용할 수도 있고, 하나만 쓸 수도 있는데, 이건 때에 따라 모델이 판단하도록 하면 좋을거 같은데. 무조건 codex를 1순위로 하고, claude를 2순위로 하는게 좋은가." The orchestrator had proposed "Codex first; claude only if Codex unavailable; both is double cost for one more Claude view". The result is `~/.claude/hooks/plan_mode_brief.md:11` (finding 4).

### E. Environment or tools the worker needs

- **Scheduling (claude skill only):**
  - Obsidian 851b67f2 L1644 (11-19)
  - Obsidian 4da78b7f L1022 (11-22)
  - Obsidian 1490b816 L2008 (11-23)
  - Agentic-Invest c27a0fd3 L118 (11-22): "스케줄링은 /claude로 하면 될테고."
  - Agentic-Invest d8c9b016 L8
- **Worker must load the user's skill:** Obsidian 851b67f2 L2154 (above).
- **Scenario runs on the production model:** Agentic-Invest 67774788 L1346 (11-26) "대조는 opus로 하게하면 되는데". The orchestrator had started contrast runs on Fable; scheduled jobs use Opus.
  - Agentic-Invest 1d5894b3 L1161, answer "13회도 Opus로".
  - SNS 4251a60e L429 and 5904a1ce L13704: test models = "Opus 5.5 + Sonnet 5.5", the models the skill serves.
- **Image generation through Codex:** Video c11422d8 L523, answer "Codex만" (rather than OpenRouter). Design and Work sessions ask for "gpt로" images (e.g. Work bab658b2 L853, 0d83d7e7 L639). This is a tool, not a worker choice.

### F. Cost or model tier (within Claude)

- Agentic-Invest 410dfeaf L7477 (10-29): "실측은 sonnet으로 하면 좋겠는데, opus로 꼭 할거야? … 학습 데이터는 유사해 지식은 꽤 비슷하거든."
- Agentic-Invest 1d5894b3 L896 (11-20): "대조를 할 때는 sonnet을 사용해도 될거 같아. 어차피 fable, opus, sonnet은 추론의 깊이는 다르지만, 학습 데이터의 양은 크게 다르지 않거든."
- SNS 5904a1ce L15113 / L15120 (11-16): "아까 opus는 안쓰고 sonnet으로만 검증을 한다고 했던거 같은데" → "Sonnet만", for half the runs and usage.
- Codex model pinned by the user:
  - Design b0e0467a L2140: "gpt-6.1-terra, high로 해야해"
  - Agentic-Invest 1d5894b3 L1109: "`gpt-6.2-sol;medium`을 사용하면 좋겠거든"
  - Agentic-Invest 8351179f L6512: "gpt-6.1-terra가 없다고?"

### G. Review not worth it (proportionality)

- motion-graphic ac103593 L499 (11-14): "디렉토리 정리에 굳이 코덱스 검토까지는 필요없을거 같아."
- Obsidian Work 5b3ce069 L442 (11-23): "굳이 코덱스의 검토를 받지 않아도 되." Codex runs had produced per-section news recommendations; the user's earlier stance is that selection is subjective (Naver-News d74d9247 L456).

### Quality complaints and speed

- **No quality complaint about a GPT worker's output was found.** The objections in B are about who should author, not about bad output; the Claude orchestrator itself judged a Codex translation segment's quality good just before (2e4ee9c1 ~L388).
- **Speed** shows up only as "don't wait for the reset".

## 2. Reasons the global rule did not capture

1. **Authoring and judgment work the user sees as Claude's job** (analysis, translation; possibly curation). The rule is read as "delegate, and default to Codex", so the orchestrator delegated work it should have done itself (2e4ee9c1 L666 response). Two per-project memories now override the rule.
2. **Workers that need the user's Claude Code environment:**
   - skills loaded in the worker (invest; `coding` via the Skill tool, which Codex lacks: run `20261116-041913-run-da9e` refused)
   - scheduling
   - scenario runs that test a Claude skill on the production model
   The hook text states this strength explicitly.
3. **Per-repo review preference for Claude with Codex available** (Ultra-Search, motion-graphic). No reason given; it is not explained by rendering.
4. **Proportionality:** small or low-risk changes don't need a second-model review, and when Codex is out the user may skip review rather than substitute (G; fb5f153a L1439).
5. **Waiting is a cost the rule doesn't price.** By November the user rejects waiting for a reset (A).
6. **Orchestrator-side reasons the user accepted, absent from the rule:**
   - separate quota pools (send bulk extraction to Codex to save Claude's quota for judging)
   - same-judge consistency across rounds: e089aa91 L6135 warns that baseline judged by Codex and new rounds judged by Claude are not comparable; L4404 and L6084 keep "앞 회차와 같은 판정자"
3. **The user wants judgment, not a ranking** (fb5f153a L537), plus cost-aware tier choice within Claude (Sonnet for knowledge probes and grading).

## 3. Run registries

Scope: 860 Codex runs and 385 Claude runs, from `.codex-runs` / `.claude-runs` under `~/Coding/*` and `~/💎Obsidian`, excluding codex in claude, claude in claude, Skill Maker and subagent in claude.
- Codex runs span 2026-08-29 → 11-27; Claude runs 10-31 → 11-27.
- Half-month volume (Codex vs Claude): Oct 2nd half 473 vs 3; Nov 1st half 157 vs 86; Nov 2nd half 87 vs 296. Claude overtook Codex in the second half of November.
- Runs in custom `--runs-dir` locations are not counted (e.g. the translation run `20261108-165850-ko4-ca75`, scratchpad `.codex-runs` used by Obsidian Work 5b3ce069).

**Sandbox and profile:**
- Codex: read-only 816, workspace-write 40, danger-full-access 4 (Obsidian invest updates `lite-c6f4`, `cohr-201c`, probe `5a72`; model-spike `8ac7`).
- Claude: read-only 352, write 15, auto 18 (the scheduled Obsidian invest runs and contrast runs use auto).

**Models:**
- Codex: gpt-6.1-terra medium 323 / high 196; gpt-6.1-sol high 123 / medium 36; gpt-6.2-sol medium 67; gpt-6-astra 86; gpt-6-luna 23.
- Claude: opus high 229 (+6 pinned `claude-opus-5-5`); sonnet high 130; fable 19.

**Purpose** (keyword heuristic on the prompt; approximate; batch fan-outs inflate counts, so groups are shown too):

| Purpose | Codex runs (groups) | Claude runs (groups) |
|---|---|---|
| Review / verification (plan reviews, code reviews, multi-round re-reviews, fidelity cross-checks) | ~604 (~283) | ~82 (~53) |
| Grading / judging | ~173 (~51), e.g. `ab-dev-judge`, `v2-judge`, `mref2-baseline-judge`, `serenity-probe-grade` | ~258 (~27), e.g. SNS `main1-a`/`pilot*` "Grade one blinded run", Invest `mref2-j12-*`, `mref2-tl-*-judge` |
| Research / extraction | ~34 (`mref2-extract`, `mref2-tl-extract`, `invest-retro`, `news-1123`) | ~12 (9 scheduled `macro-*`/`portfolio-*`/`inv-ant-*` invest runs, 3 extraction) |
| Implementation | ~40 write-sandbox runs, e.g. SNS P4–P7 runs, `pstack-scenes` rounds, `invest-merge`, `v2-label` | ~7 (SNS `p6-output`, P6 help `run-3c59`, `r11-95d4`, `run-1f63`; 3 `pstack-polish`) |
| Scenario / contrast runs | ~1–2 probes | ~25 (`skill-contrast`, `contrast-a*`, `imagegen-contrast-t*`, `schema-contrast-1112`) |

The split is clear: **Codex is the verifier and fidelity checker; Claude is the grader and the scenario runner** (anything that must exercise a Claude skill), and Codex was the main implementer until mid-November.

**Failures:**
- Codex: 77 failed, about 76 of them on the usage limit (a "You've hit your usage limit" turn failure); 2 interrupted (COHR stopped by user, plus one other).
- Claude: all 43 failed runs are "claude exited" with the rate-limit field status=rejected, i.e. the Claude limit; 7 interrupted; 1 timed out (`macro-45609590126` scheduled run); 1 orphaned.
- Both engines fail mainly on quota.

**Capability limits visible in final messages:**
- **Both engines:**
  - Chrome won't start in the sandbox. Claude: `20261113-232654-review-core-3b7e`, `20261115-003810-ibm-batch2-review-e681`, `20261123-224348-review-same-frames-6d18`, `…233127-review-same-frames-2-4a48`. Codex: `20261015-003745-graphic-review-229c`, `20261015-010300-graphic-review-47b0`.
  - Localhost bind denied. Claude: `20261113-232238-pra-review-be56`, `20261031-133515-rereview-d9bd`. Codex: `20261118-204142-pr4-review-4b45`, `20261009-061434-review-result-wait-b305`.
- **Codex only:**
  - PyPI/uv DNS and cache blocks: `20261016-085447-review5-84c4`, `20261116-041913-run-da9e`, `20261115-202729-p5-results-9870`.
  - No Skill tool: `run-da9e`.
  - Turn ended while the test suite still ran: `20261115-200515-p5-renames-9df7` → follow-up `…-21eb`.
  - Resume that did nothing: `20261116-042615-run-de8a`, briefed "Your working tree is still clean: nothing was implemented".
- **Claude only:** the `write` profile is denied edits under `.claude/skills/` and `.claude/plans` (`r11-95d4`).

**Work redone with the other engine:**
- **Obsidian:** COHR (Codex `cohr-201c` stopped and discarded) and LITE (Codex `lite-c6f4`, merged in PR #151) were both redone with Claude.
- **Translation `ko4-ca75`:** discarded, redone by the orchestrator itself.
- **SNS Facebook surfaces:** Codex `da9e` → `750a` → `de8a` produced nothing, then hit the limit; redone by Claude `20261116-042641-run-1f63` (6 of 10 items, suite 3406 passed).
- **Invest TraderLion:** the Codex fidelity check was waiting on quota; a Claude worker checked only the 25 changed lines instead (e089aa91 L6184).

## 4. Continuing one engine's work with the other

The handoff was always through the working tree or git plus a short preamble. Thread resume across engines was never used.

- **Codex → Claude, SNS R11.** Codex `20261116-134137-run-dbc3` hit the limit mid-implementation. The orchestrator first ran `git status`, then started Claude Opus `write` worker `20261116-134251-r11-95d4` in the same worktree (Agentic SNS 5904a1ce L12497–L12505).
  - Preamble: "A previous worker (another model) started this task in this directory and stopped at its usage limit partway: the working tree holds its uncommitted, unverified partial change (git status / git diff shows it). Its last note: … Treat that change as a draft: check every part of it against the brief below, keep what is right, fix or finish the rest. … Then the brief, unchanged."
  - Outcome: the worker couldn't write `.claude/skills/` under the `write` profile and left `naver-blog-news-source.patch`.
- **Codex → Claude, motion-graphic pstack.** Codex group `pstack-scenes-r3` (felt/set/sketch) failed on the usage limit. Within a minute the orchestrator (motion-graphic 24d1e0de L1277–L1303) launched Claude Sonnet `write` workers `20261113-232301-felt-2e23`, `-set-927e`, `-sketch-74af`.
  - Brief: "the scenes of your family already exist from two earlier rounds by another worker and are mostly right in framing and beat order … the last polish", "RULES (unchanged from the earlier rounds)".
  - A render service outside the sandbox stood in for Chrome.
- **Codex → Claude, from scratch.** SNS Facebook: the same brief was re-sent to Claude with "Environment notes: invoke the `coding` skill with the Skill tool … You are a Claude worker; no other model reviews this before me." Nothing to carry over: Codex had left the tree clean.
- **Claude → Codex, Video 9403c114 L2497.** The Codex thread was resumed after its limit (`20261114-093251-prb-review-4df3`): "Your last turn in this thread could not run (usage limit). Since then PR A was reviewed by a Claude worker only and merged as #9 (`git show d1fbb5b`), and PR B is implemented on branch `docs/skill-facts` (`git diff main..docs/skill-facts`)." Context went via git refs and the plan path.
- **Planned but not observed:** Invest e089aa91 L5410/L5443 promised "실패한 장만 바로 클로드 작업자에게 넘기고, 어느 장을 누가 뽑았는지 기록". The Codex extraction finished without hitting the limit (L5683 check).

## What I could not determine

- Why the user preferred Claude for review in Ultra-Search (11-07) and motion-graphic (11-14 and 11-23/26). No reason is stated anywhere, and Codex was available.
- Whether SNS 11-15 L5406 was quota-driven; the timing is ambiguous.
- Purpose counts are heuristic and indicative only (prompt keywords plus sandbox mode). Codex "review" includes many re-review rounds of one change; I did not hand-label all 1,245 runs.
- Runs launched with a custom `--runs-dir` (scratchpads, the video ko/runs folder) are missing from the registry counts.
- Headless worker sessions and the scenario-run tmp project directories were excluded, as instructed, so their own reasoning isn't reflected.

No files were modified. My scratch scripts are only under `$TMPDIR/ev`.
