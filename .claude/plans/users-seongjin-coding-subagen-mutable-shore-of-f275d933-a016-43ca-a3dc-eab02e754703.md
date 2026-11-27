# delegate 스킬: codex·claude 작업자를 한 스킬로

> **구현 세션 첫 동작:** `ToolSearch("select:TaskCreate,TaskUpdate,TaskList")`로 트래커를 열고 아래 "단계"와 "전환"을 태스크로 만든다(description = 그 단계의 완료 판정). `coding`·`skill-maker`·`codex`·`claude` 스킬을 `Skill`로 연다(성진: 구현에 `/coding`을 쓰고, `codex`·`claude` 작업자로 검토·검증한다). 전환 전까지 위임에는 설치된 옛 `codex`·`claude` 스킬을 쓰고, 전환 뒤에는 `delegate`로 쓴다. PR ①에서 이 계획 파일과 근거 두 개를 레포에 남긴다: `uv run "/Users/seongjin/.claude/skills/codex/scripts/cli.py" result --run 20261127-175435-merge-map-93cb`의 본문 → `.claude/plans/261127_delegate-merge-map.md`, `uv run "/Users/seongjin/.claude/skills/claude/scripts/cli.py" result --run 20261127-175433-routing-evidence-41fc`의 본문 → `.claude/plans/261127_delegate-routing-evidence.md`(둘 다 이 레포의 git이 무시하는 레지스트리에만 있다).

## Context

성진은 두 스킬을 따로 만들어 써 왔다: `codex`(Codex CLI·GPT, v0.11.0, `/Users/seongjin/Coding/codex in claude`, HEAD `e8461a1`)와 `claude`(headless Claude Code, v0.4.0, `/Users/seongjin/Coding/claude in claude`, HEAD `7337323`). 둘은 같은 설계(분리 supervisor, 즉시 돌려주는 핸들, `result --wait` 한 호출 대기, resume·stop·batch·worktree·clean·doctor, JSON 한 줄 출력과 같은 종료 코드 계약)를 엔진마다 따로 구현했고, 어느 엔진을 쓸지는 전역 CLAUDE.md의 "위임·검증 기본값은 codex"와 성진의 즉석 정정("쿼터 찼으니 /claude")에 흩어져 있다.

이 계획은 둘을 **`delegate` 스킬 하나**로 새로 쓴다: 엔진(GPT=Codex CLI / Claude=`claude -p`)은 시작할 때 고르는 인자이고, 나머지는 한 명령 표면·한 레지스트리다. 한 batch에 두 엔진을 섞을 수 있다. 기본 엔진·기본 모델은 없다 — 오케스트레이터 클로드가 SKILL.md의 엔진 사실과 `models`가 보여 주는 지금의 모델·한도를 보고 매번 고른다. 한 엔진이 멈춘 일을 다른 엔진이 이어받는 법은 SKILL.md의 판단으로 담는다. 결과물은 skill-maker의 프레임(여섯 속성, 「Code the skill bundles」, 「When the skill is done」)을 철저히 따른다. 코드는 `https://github.com/tjdwls101010/SubAgent-in-Claude`(public, 이 레포)에 플러그인으로 두고, 구현을 마치면 v0.1.0을 릴리스한다. 성공 기준은 품질 하나다.

## 합의 장부

### 결정

| 내용 | 출처 |
|---|---|
| 목표 = 위 Context. 10/31 claude 계획의 "codex 스킬과 독립·라우팅 없음"을 뒤집는다 | 성진 |
| 성공 기준으로 고른 것: **섞인 그룹**, **엔진 선택 판단**(SKILL.md가 GPT/Claude의 경계를 담는다) | 성진 |
| 이름: 스킬·플러그인 `delegate`, 레포 `SubAgent-in-Claude` 유지. `subagent`는 Claude Code 내장 서브에이전트(Agent 도구, `.claude/agents`)와 트리거가 겹치고 두 원본 description도 그것을 제외했다. "delegate"는 Claude Code 문서에서 서브에이전트에 일을 넘기는 동사로 쓰이지만 기능·모드 이름은 아니다(`claude in claude/.tmp/docs_claude` grep) | 성진(클로드 추천) |
| **기본 엔진 없음.** start·batch의 새 멤버·schedule는 엔진을 명시한다. 전역 CLAUDE.md의 "위임·검증 기본값은 codex"도 없앤다(초안 승인 뒤) | 성진 |
| **스킬 문서·help·코드에 모델 이름(slug·별칭)이 없다.** 새 시작(start·batch 새 멤버·schedule)은 **`--model`과 `--effort` 모두 필수**, resume은 기록값을 잇는다(실측: `claude -p`는 `--effort`가 없으면 settings `effortLevel`을 따르고 init에 effort가 안 나와 "모델 기본값"을 알 수 없다 — 그래서 처음 합의한 "안 주면 모델 기본값"을 대체). 쓸 수 있는 모델과 모델별 effort는 `models`가 두 CLI에서 런타임에 읽는다. 10/31의 "sonnet·high 고정"을 대체 | 성진(클로드 추천) |
| 대조 결과 12개 모두 괜찮다 → 엔진 선택 절은 순위·규칙 없이 **클로드가 못 보는 사실**만 싣는다. 번역·분석은 클로드가 직접 하라는 과거 지시는 Obsidian 프로젝트 메모리의 선호로 남는다 | 성진 |
| Ultra-Search·motion-graphic의 "검증은 claude로"는 그때의 한도 사정 → 새 판단 없음 | 성진 |
| 한도: 엔진별 남은 한도를 `models`·`doctor`에 드러내고, **바닥난 엔진으로의 시작은 초기화 시각과 다른 엔진 안내와 함께 거부한다**(두 엔진 모두). 자동 전환은 없다(교차 검증이 같은 계열 검토로 조용히 바뀌지 않게). 바꿀지는 오케스트레이터가 판단하고 한 줄로 알린다 | 성진(클로드 추천) |
| 기능 확장: codex 런에도 attention(이른 반환), 두 엔진 한도 표시. codex 작업자 예약·교차 검토 단축 표기는 넣지 않는다(쓰인 근거 없음 — 필요해지면 다시) | 성진(클로드 추천) |
| 엔진 인계는 **명령이 아니라 판단으로** 넣는다: SKILL.md 한 절과, 기록과 다른 엔진으로의 `resume`을 거부하는 응답의 `instead`. 근거: 실제 인계 4번 모두 오케스트레이터가 이전 런을 `result`로 읽고 상황에 맞는 머리말을 직접 써서 해냈고 머리말은 매번 달랐다 — skill-maker Model over machinery. 처음 고른 "`start --from` 같은 명령"을 대체 | 성진(클로드 정정 추천) |
| SKILL.md는 한 파일(references/ 없음). 예약 절도 SKILL.md에 둔다 | 성진 |
| 작업자는 어느 엔진의 작업자도 띄울 수 없다(재귀 금지, 두 엔진 공통). 현재 실측 0건 | 성진(클로드 추천) |
| 옛 `.codex-runs`·`.claude-runs`는 런타임에 읽지 않는다. 옛 스레드·세션은 native id로 resume한다(아래 resume 계약). 예약 이행 잡만 이행 시점에 한 번 읽는다 | 성진(클로드 추천) |
| **레지스트리는 각 프로젝트(저장소 본 체크아웃)의 `.delegate-runs/`** — skill-maker의 `data/` 기본과 다른 예외: 런은 그 프로젝트에 속하고(worktree가 그 저장소 안에 생기고, 예약 러너의 Finish가 그 프로젝트에서 런을 찾고, 프로젝트를 지우면 런도 사라짐), 심링크 설치에서 `data/`면 모든 프로젝트의 런이 이 레포 폴더에 쌓이고 플러그인 업데이트와도 엮인다. 두 원본과 같은 방식 | 성진(클로드 추천) |
| 기존 스킬: 로컬 설치만 새 스킬로 바꾸고, 원본 두 레포·공개 플러그인은 손대지 않는다 | 성진 |
| 서 있는 예약은 새 스킬로 이행한다(아래 "전환"). 이미 실행을 마친 일회성 예약은 다시 쓰지 않고 **지운다**(워크플로 파일과 dispatcher; Obsidian 선례 `1f4bb058a`). 옛 `~/.claude/skills/claude` 심링크는 전환 완료 판정 뒤에 치운다 | 성진(클로드 추천) |
| **코드는 새로 쓴다(구조 새로, 지식은 가져옴).** 공통 계층(명령 표면·레지스트리·그룹·대기·엔진 인터페이스)은 새로 설계해 쓴다. 두 스위트의 픽스처·가짜 CLI·행동 시나리오는 새 코드가 통과할 명세로 가져오고(아래 행동 보존 표), 남의 형식 단위(claude 스트림 해석, codex 이벤트·argv·config·catalog, GitHub 워크플로 형식, launchd)는 새 인터페이스에 맞춰 고쳐 쓴다(내용은 가져와도 된다). 가져온 지식은 커밋 본문에 원본 SHA·경로로 적는다. 코덱스 병합 지도는 claude 코드베이스 이식을 추천했으나 성진이 이 방식을 골랐다 | 성진 |
| 계획과 구현에서 `codex`·`claude` 작업자로 여러 측면을 살핀다. 구현 중 `coding` 스킬을 쓴다 | 성진 |
| 구현을 마치면 릴리스한다(v0.1.0 태그 + GitHub 릴리스) | 성진 |
| 예약 실 스모크는 **전용 private 레포를 새로** 만들어 돌린다(구현 세션이 `gh repo create --private`로 만들고, 성진이 그 레포의 self-hosted runner를 이 맥에 서비스로 등록 — 구현 세션은 그때 멈추고 등록 절차를 안내한다). Obsidian·기존 레포는 쓰지 않는다 | 성진(클로드 추천) |

### 사실 (출처)

- 원본 규모: claude 7,377줄·테스트 7,004줄, codex 3,540줄·테스트 4,496줄. 가짜 CLI·픽스처: `claude in claude/tests/support/{fake_claude,fake_gh,fake_launchctl,fixtures,harness.py,names.py}`, `codex in claude/tests/support/{fake_codex,fixtures,legacy_registry,harness.py}`. 헤드리스 대조 러너: 두 레포 `tests/e2e/{run_e2e.py,scenarios.md}`. 실 스모크: `smoke_real_claude.py`, `smoke_real_schedule.py`(약 35분, 본인 private 레포·runner·launchd), `smoke_real_codex.py`.
- **병합 지도**(`.claude/plans/261127_delegate-merge-map.md`로 옮김; 공통 계층 설계의 1차 자료): 드라이버는 둘 — codex는 stdin 닫힌 `exec` 한 번 + 최종 메시지 파일(`-o last-message.txt`), claude는 stdin을 열어 두고 어느 메시지에 답했는지 추적하며 프로세스가 살아 있는 동안 답이 확정될 수 있다(`finishing`). 권한은 엔진 태그 설정이어야 하고(codex `read_only: scratch|strict`·버전 하한 격리 0.122.0/프로필 0.160.0, claude `profile_exceptions`·`mode_mismatch`), 그룹 의미가 다르다(codex는 batch가 기록한 런, claude는 멤버 세션의 최신 런). worktree 방식: codex는 스킬이 `<run_dir>/wt`에 detached checkout, claude는 Claude Code `--worktree`가 `.claude/worktrees/<name>`·브랜치 `worktree-<name>`. codex에만 있는 회귀: 재사용된 pid(`d00fb5d`), 서브모듈 linked worktree의 레지스트리 위치(`a53e26a`), 중단된 `git worktree add` 복구(`1a8f68a`), 원본 사라진 worktree 정리. Claude만: 시작 중 stop 게이트, 세션 잠금 안 worktree 재검사, `.worktreeinclude` 복사본·시작 산출물 지문으로 남은 파일 보존, 예약 leftover를 `refs/claude/leftovers/*`에 보존.
- **라우팅 증거**(`.claude/plans/261127_delegate-routing-evidence.md`로 옮김): 실사용 codex 860런·claude 385런. 쓰임: codex = 검증·충실도 대조(~604), claude = 채점(~258)·시나리오/대조(~25). 실패는 두 엔진 모두 거의 한도(codex 77 중 76, claude 43 전부). 성진의 엔진 개입 약 40건의 사유: 한도(가장 많음), 작업자에게 성진의 Claude Code 환경이 필요(사용자 스킬 — codex에는 Skill 도구가 없어 런 `20261116-041913-run-da9e`가 구현을 거부; 예약; Claude 스킬 시나리오는 쓸 모델로), 교차 계열 검증, 비례성, 같은 회차는 같은 판정자, 한도 풀 나눠 쓰기. 성진은 고정 순위가 아니라 판단을 원한다(-claude fb5f153a L537).
- 크로스 엔진 인계 4건은 모두 **같은 디렉터리 + 작업 트리/git 상태 + 이전 작업자의 마지막 말 + 바뀌지 않은 브리프**로 했다(머리말 실례: Agentic SNS 5904a1ce L12497 "A previous worker (another model) started this task in this directory and stopped at its usage limit partway: the working tree holds its uncommitted, unverified partial change … Treat that change as a draft: check every part of it against the brief below, keep what is right, fix or finish the rest.").
- claude `write` 프로필: `.claude/skills/` 안 셸 쓰기는 샌드박스가 거부(`operation not permitted`, `denyWithinAllow`; 실측 `20261127-182236-probe-write-c282`), Edit 도구 거부는 런 `r11-95d4`. `auto`의 경계는 미확인.
- 두 엔진 모두 샌드박스에서 Chrome 실행·localhost bind가 막힌다(claude `20261113-232654-review-core-3b7e`, codex `20261015-003745-graphic-review-229c`).
- **한도 읽기**: codex는 `$CODEX_HOME/sessions/**/rollout-*.jsonl`의 `token_count` 이벤트 `rate_limits`(`limit_id`가 `codex`와 `premium`으로 나뉨, `primary` 5h·`secondary` 7d의 `used_percent`·`resets_at`, `plan_type`, `rate_limit_reached_type`) — `exec --json` 이벤트에는 없다. 설치된 0.167.1 바이너리에 `account/rateLimits/read`(app-server RPC)가 있으나 인증된 응답은 미확인. claude는 `claude -p "/usage" --safe-mode --no-session-persistence`(턴 0, 비용 0, 약 2초, `usage_report.rate_limits.limits[]`, 원본 파서 `claude/claude_cli/install.py:84`가 모델 범위·초과 사용 설정을 보존)와 스트림의 `rate_limit_event`.
- **codex 한도 실패의 기록**: `{"type":"error","message":"You’ve hit your usage limit. … try again at Nov 14th, 2026 4:01 AM."}` 뒤 `turn.failed`(motion-graphic `20261113-231810-sketch-r3-823f`). `exec --json`에는 재시도·재연결 이벤트가 없다(전 레지스트리 error 이벤트: 한도 78, 서비스 티어 경고, 400 invalid_request).
- **모델 목록**: `codex debug models`(12개; `slug`·`description`·`priority`·`context_window`·`supported_reasoning_levels`·`default_reasoning_level`·`upgrade`·`visibility`(list/hide), 가격 필드 없음, 모델마다 `base_instructions`가 들어 있어 거대). **카탈로그에 있어도 계정이 못 쓸 수 있다**: "The 'gpt-6-astra' model is not supported when using Codex with a ChatGPT account."(Agentic Invest `20261011-022703-review1-rs-v2-afd3`, 첫 턴 400). claude는 `claude -p "/model" --safe-mode --no-session-persistence --output-format json`이 모델 호출 없이(턴 0, $0) "Available: sonnet, opus, haiku, fable, best, sonnet[1m], …, or a full model ID"를 준다. 별칭은 계열의 최신을 가리킨다. (claude 2.1.332, codex 0.167.1)
- `codex exec resume --help`에는 `-C`·`-s`가 없다 — 원본 codex는 프로세스 cwd와 `-c`로 설정을 재주입한다(`codex/codex_cli/argv.py`). `claude -p --resume`은 모델은 복원하지만 권한 모드와 스키마는 복원하지 않고, 세션을 프로젝트 디렉터리로 찾는다(원본 `resume --help`, 10/31 계획 exp5). codex `app-server`에는 `turn/steer`가 있다(이 스킬은 `exec`만 쓴다).
- **서 있는 예약 40개**(`tjdwls101010/Obsidian`, 로컬 `/Users/seongjin/💎Obsidian`): 반복 2개(`매일_MACRO_개장전점검`, `매일_PORTFOLIO_매도조건점검`) + 일회성 38개(2027-06까지). 모두 `model: opus`·`profile: auto`·worktree. launchd `~/Library/LaunchAgents/claude-schedule.tjdwls101010-Obsidian.<name>.plist`(XML)가 `gh workflow run <name>.yml --repo tjdwls101010/Obsidian --ref main -f source=dispatcher`를 부르고, self-hosted runner가 돌린다. 워크플로 파일은 헤더 "# Written by the claude skill's `schedule` command…", `# spec:` JSON(`cli`는 **심링크 경로 `/Users/seongjin/.claude/skills/claude/scripts/cli.py`**), 두 `run:` 줄(`uv run <cli> __scheduled run|finish … --profile auto … --definition <hash>`), env `CLAUDE_SCHEDULE_MODE/SOURCE/PROMPT`, `concurrency.group: "claude-<name>"`, `queue: max`를 담는다. 일회성 cron은 연도가 없다(`'30 21 28 11 *'`). 일회성 Finish는 기록된 `definition`이 맞을 때만 워크플로를 비활성화한다(`claude/schedule/execute.py:383-385`, 지우지는 않음 `:391`); `schedule`을 다시 부르면 비활성 워크플로를 다시 켠다(`define.py:234-239`); 새 definition은 `keep_definition` 없이는 새로 뽑힌다(`spec.py:170`). 예약 상태 `.claude-runs/.schedules/<name>/state.json`(`definition`·slots·`jobs` → 옛 런 id; 재시도 판단은 `find_by_origin_job`으로 런 meta를 읽음 `registry/schedules.py:48`). 원본은 워크플로를 Contents API로 파일마다 한 커밋 쓴다. 지금 Obsidian에는 `refs/claude/leftovers/{macro,portfolio}-*` 5개와 `.claude/worktrees/portfolio-45820211058-1-1 [prunable]`가 있고, 볼트 본 체크아웃에는 성진의 미커밋 변경이 있다. 원본의 직전 전환 계획은 `queued`도 확인했다(`claude in claude/.claude/plans/261123_claude-skill-schedule-local-sync.md:5`).
- **`~/.claude`는 git 레포**(원격 `tjdwls101010/Agentic-Coding`): `CLAUDE.md`, `settings.json`, `hooks/**`, `skills/*` 심링크(`skills/claude`, `skills/codex` 추적됨), `skills/coding/SKILL.md`(실제 디렉터리), `README.md`(56·57행 심링크 표)를 추적한다. 지금 ` M settings.json`(Orca 훅, 무관한 162줄)과 `?? skills/sns`가 있다. 선례 PR #33 "claude 스킬을 codex의 대체 위임 경로로 등록한다".
- 옛 스킬 언급: `~/.claude/CLAUDE.md:37-38`(`codex`·`claude` 항목), `~/.claude/hooks/plan_mode_brief.md:9`("codex·claude 작업자 실행")·`:11`(계획 검토자 선택, "위임·검증 기본값은 codex를 이 판단이 대신한다"), `~/.claude/skills/coding/SKILL.md:68`("through the `codex` skill … get that review"), `~/.claude/settings.json:13-16`(허용), `~/.claude/README.md:56-57`. `plan_mode_skill_maker.md`·`hooks/*.py`에는 없다. 훅 테스트 `~/.claude/hooks/tests/test_plan_mode_hooks.py`.
- 이 레포 위생: `git ls-tree HEAD`에 심링크 `.claude/histories`(→ `~/.claude/projects/…`), `.tmp/claude in claude`, `.tmp/codex in claude`가 추적돼 있다(public 레포). 빈 `.claude/skills/subagent/SKILL.md`도 추적돼 있다. 원본 claude 레포는 `.claude/histories`·`.claude/worktrees/`를 무시한다.
- 원본의 확립된 관례(유지): SKILL.md는 영어, description에 한국어 트리거; 테스트 러너 unittest, 레포 `tests/`; 기대값은 실캡처 픽스처에서(대상 코드에서 끌어내지 않음); 계획 파일은 그 변경의 PR에 넣는다(claude 원본처럼). `claude plugin validate --strict`는 2.1.332에 있고 원본 `…/.claude/skills`와 레포 루트 둘 다에 exit 0.

### 대조 실행 (엔진 선택 절의 근거 — 원문)

skill-maker 「Contrast with the default」. 빈 디렉터리에서 `claude -p --safe-mode --restricted --permission-mode acceptEdits --model opus --output-format json "<task>"`, $0.10, permission_denials 없음. 프롬프트 원문(판단·선호는 뺐다; S6에서 그대로 다시 쓴다 — "handed over … with the previous run's record"는 인계를 명령으로 넣기 전 표현이므로 S6에선 "a fresh run on the other engine can continue in the same directory"로 바꾼다):

```
You are the orchestrating Claude Code session of a user who works with you over many turns. Besides your own tools (including your built-in Agent subagents), you have one delegation skill that starts managed background workers on either of two engines. Facts about them:

- **GPT worker** (OpenAI Codex CLI, `codex exec`): a different model family from you. Runs isolated: it does not load the user's Codex config, so no MCP servers, plugins, skills or hooks; it has no Skill tool. Sandboxes: read-only (reads anything, can run tests writing only temp/cache, no network), workspace-write (writes in its directory, no network), danger-full-access. Uses the user's ChatGPT subscription quota (5-hour and 7-day windows, separate from Claude's). Its model catalog lists several GPT models with descriptions (most capable / fast everyday / affordable). Cannot be messaged mid-turn. Can generate images.
- **Claude worker** (headless `claude -p`): same model family as you. Inherits the user's whole Claude Code environment: global and project CLAUDE.md, user skills (callable with the Skill tool), hooks, plugins, MCP servers, memory. Profiles: read-only (reads anything, runs shell in a sandbox writing only temp/cache, no network), write (edits its directory; edits under `.claude/skills/` and `.claude/plans` are denied), auto (classifier-reviewed, network). Uses the user's Claude subscription quota (shared with you). Models: Claude aliases. Can be messaged mid-turn. Can be scheduled to run unattended later.
- Both: run in the background with a handle; you wait for and collect the result; several can run as one group, possibly mixed across engines; a run on one engine can be handed over to a fresh run on the other with the previous run's record. In both engines' sandboxes Chrome cannot start and binding localhost is denied.

For each situation below, decide what you would do: do it yourself (no worker), use your built-in Agent, start a GPT worker, a Claude worker, both, or nothing (skip). Say which kind of model (most capable / balanced / cheapest) and permission level, and give your reason in one or two sentences. Do not ask questions; decide.

1. The user asks you to translate the English subtitles of a 40-minute video (an .srt file in the repo) into Korean, keeping timing lines.
2. In an Obsidian vault, the user keeps per-ticker investment analyses written with their `invest` skill. Six tickers need updating after this week's earnings. The user wants them updated.
3. You wrote an implementation plan for a 6-PR feature in an unfamiliar codebase. Before handing it to an implementer, you want it reviewed.
4. You are evaluating a skill change: 40 blinded output files must be graded against a rubric. Rounds 1–3 of this evaluation were graded by Claude Sonnet workers.
5. A large implementation task (one PR from a plan file) must be done. The repository's CLAUDE.md says implementers must open the `coding` skill with the Skill tool before writing code.
6. You need facts extracted from 14 book chapters (PDF text already in the repo) into a structured JSON per chapter. Claude's 7-day quota is at 81%; GPT's is at 12%.
7. You just moved some files into a new directory structure (no logic changes) and are about to commit.
8. A GPT worker implementing a PR stopped at its usage limit halfway; its uncommitted partial changes are in the working tree. GPT's limit resets in 2 hours.
9. You changed a Claude Code skill's SKILL.md and want to see whether the model that will use it (the user's sessions run Opus) now behaves as intended on five scenario prompts.
10. A small bug-fix PR (30 lines) in a search tool repository is ready and you want a review before merging.
11. Every weekday at 21:00 the user wants a check of macro market conditions written into their vault, using their skills and MCP connectors.
12. A plan review by a GPT worker came back with 12 findings; you fixed 9 and rewrote several paragraphs. You want to know whether the plan is now ready.

Answer as a numbered list, one entry per situation.
```

(이 프롬프트의 "Can generate images"는 미확인 사실이라 S6에서는 뺀다.) 기본 클로드의 선택(성진은 12개 모두 받아들임 — S6의 판정 기준): 1 GPT 균형(한도 아끼기) / 2 Claude 6개 그룹 최고·auto(invest 스킬) / 3 GPT 최고·read-only(다른 계열) / 4 Claude Sonnet 그룹(같은 판정자) / 5 Claude 최고·write(GPT엔 Skill 없음) / 6 GPT 그룹(한도 풀) / 7 직접 / 8 Claude로 인계, 기다리지 않음 / 9 Claude Opus 그룹 read-only(쓸 모델) / 10 직접 리뷰 / 11 Claude 예약 auto / 12 새 GPT 최고 + 원래 지적 12개. **결론: 사실을 주면 기본 클로드가 성진처럼 나눈다 — 엔진 선택 절은 이 사실(구현 뒤 실측한 지금 값으로)을 정확히 싣는 것이 일이다.** 이 12개는 SKILL.md 검증(S6)에서 다시 쓴다.

## 설계

### 엔진 인터페이스 (`engine/`, 병합 지도 §2와 두 리뷰의 계약)

어댑터(claude, codex)가 소유하는 것: 실행 cwd, 프롬프트 전달 방식(claude stdin stream-json / codex `--` 뒤 인자·stdin DEVNULL), stdin 수명과 메시지 전달·응답 확인(claude만), 출력 산출물(events, codex `last-message.txt`), 시작 확인(claude init / codex thread id, 최대 15초), 종료 판정, 권한 설정의 argv·env·settings 번역, 재주입할 것(claude: 프로필·도구 금지·env 정책·모델·effort·스키마 / codex: sandbox·격리·모델·effort·service tier·스키마), 한도 관측 읽기, 모델 목록 읽기, 진단. 공유 계층이 받는 것: `answer_ready`(답 확정), `alive`(프로세스 생존), `terminal`(종료 결과)을 **따로**, 그리고 attention 후보와 한도 관측. 설정은 엔진 태그가 붙은 타입이고 공통 enum으로 합치지 않는다.

### 명령 표면 (`scripts/cli.py`)

공통: stdout은 명령마다 아래 형식(대부분 JSON 한 줄), stderr는 진행·진단. 종료 코드 0 성공 / 1 상태가 거부했거나 요청한 일이 실패(`error`, 대안이 있으면 `instead` — 대안 명령은 받은 `--project`·`--runs-dir`를 보존) / 2 명령줄을 고쳐야 함(`help`가 읽을 `--help`) / 3 `doctor`가 blocker. **`result`는 읽었으면 런이 실패했든 `--wait-timeout`이 지났든 0, `batch`는 모든 멤버 시도를 마쳤으면 일부 실패가 있어도 0**(실패는 JSON에). 모든 명령에 `--project DIR`·`--runs-dir DIR`. 작업자 안(마커 env, 두 엔진 공통 주입)에서 `start`·`resume`·`batch`·`schedule`은 exit 1(관리 CLI 진입점의 차단이다; claude 작업자는 원본처럼 Monitor·SendMessage·PushNotification·CronCreate·RemoteTrigger도 금지하고, `Skill(delegate)`·`Skill(claude)`·`Skill(codex)`도 금지). 옛 스킬이 설치된 전환 기간에는 delegate 작업자에게 옛 claude·codex 스킬의 작업자 마커도 함께 넣어 옛 CLI로 우회하지 못하게 한다(원본 코드에서 그 이름을 읽어; 옛 스킬 삭제 뒤 제거). 각 `<command> --help`는 자기 인자·출력·실패·종료 코드를 혼자서 말한다(argparse는 부모 epilog를 최상위에서만 보임). 원본 두 CLI에 있던 입력 가운데 아래 표에 없는 것은 PR에서 유지 또는 의도적 삭제를 명시한다(예: codex `--priority`는 유지).

| 명령 | 인자 | stdout |
|---|---|---|
| `start` | `PROMPT` \| `--prompt-file F` \| stdin; **`--engine {claude,codex}` 필수**; **`--model M` 필수**; **`--effort E` 필수**; `--profile {read-only,write,full}`(기본 write); `--schema F`; `--timeout SEC`; `--label L`; `--cwd DIR`; `--priority`(codex만, 다른 엔진이면 exit 2) | JSON 한 줄: `run_id`, `engine`, `native_id`(codex는 15초 안에 안 오면 null), `state`, `next`(`result --run <id> --wait`, `run_in_background: true`), `profile`, `model`, `effort`, `cwd`, `concurrent_writers`, `quota`(`state: ok\|exhausted\|unknown`, `scope`, `observed_at`, `source`, `resets_at`), 엔진별(codex: `sandbox`, `read_only`, `read_only_note`, `isolated` / claude: `permission_mode`, `mode_mismatch`, `profile_exceptions`), `events`; 시작 중 실패면 `next` 대신 `error`·`stderr_tail` |
| `resume` | `REF`(run id·native id·고유 접두); 프롬프트는 위치 인자·`--prompt-file`·stdin; `--cwd`·`--profile`·`--model`·`--effort`·`--schema`·`--timeout`·`--priority`는 기록값을 덮음. **레지스트리에 없는 native id**: `--engine`·`--profile`·`--cwd` 필수, codex는 `--model`·`--effort`도 필수(격리 런은 config를 읽지 않아 기록이 없으면 이을 값이 없다), claude는 세션이 모델을 복원하므로 `--model`은 선택이고 `--effort`는 필수(세션이 effort를 복원하지 않으면 settings로 떨어지므로 — 단계 1에서 실측) — 엔진이 복원하는 것과 호출자가 주는 것을 help가 말한다. 기록과 다른 `--engine`은 exit 2 + `instead`(다른 엔진에서 같은 `--cwd`로 새로 `start`하고 이전 런의 `result --run <id>`를 읽어 브리프에 넣으라는 문장과 그 두 명령) | start와 같은 모양 |
| `send` | `REF MESSAGE` \| `--prompt-file`; `--interrupt` | 전달 기록. codex 런은 exit 1 + `instead`(기다리거나 `stop` 뒤 `resume`) — 이 스킬의 codex 백엔드(`codex exec`)에 턴 중 조종 수단이 없어서 |
| `batch` | `--group NAME`; `--task P`(반복) \| `--tasks-file F`(JSONL: `prompt, kind(start\|resume), resume, engine, model, effort, profile, schema, cwd, label, priority`); 그룹 기본값 `--engine --model --effort --profile --schema --timeout --cwd`; `--worktree`. **`--profile full`은 그룹 기본값이 될 수 없다**(exit 2; 태스크 줄마다 명시 — 섞인 그룹에서 codex 쪽이 샌드박스 없이 도는 것을 한 플래그로 정하지 않게) | JSON 한 줄: `group`, `spawned`, `requested`, `next`, `runs`(슬롯마다 `index, label, engine, run_id, state, cwd, worktree, profile` + 엔진별 필드, 또는 `error`), `worktrees`/`shared_tree`, `missing_ignored`. 새 멤버가 엔진·모델·effort를 결정하지 못하면 아무것도 시작하기 전에 exit 2 |
| `status` | `[--run REF \| --group NAME]` | JSON 한 줄: 목록 또는 한 런·그룹(상태, 경과, 마지막 말, attention, 한도 관측) |
| `log` | `--run REF` | 텍스트: 한 형식의 "한 번 보기"(수명주기, 메시지, 명령과 종료 코드, 바뀐 경로, 오류), 끝줄 `run=<id> state=<state>` |
| `result` | `--run REF` \| `--group NAME`; `--wait [--wait-timeout SEC]` | 런: JSON 헤더 한 줄(공통 `run_id, engine, native_id, state, exit_code, error, attention, returned_because, files_changed, usage, message_bytes, note, next` + 엔진별: claude `answered, unanswered, permission_denials, files_edited, files_edited_note, followups, cost_usd, blocked_by_hook…` / codex `turn_failed, commands`) 다음 최종 메시지. `--schema` 런은 들여쓴 JSON 문서 하나(`json`). 그룹: JSON 헤더(`group_state, returned_because, overlaps, totals, attention, members, next`) 다음 멤버마다 `--- [<index>:<label>] engine=<e> run=<id> state=<s> bytes=<n>`과 4000B로 자른 메시지 |
| `stop` | `--run REF` \| `--group NAME` | JSON 한 줄: 멈춘 런들 |
| `clean` | `--group NAME [--force]` | JSON 한 줄: 지운 worktree, 남긴 것과 이유(점유한 런 id 포함) |
| `models` | `[--engine E]` | JSON 한 줄: 엔진마다 `models`(codex: slug·description·efforts·default_effort·context_window·visibility / claude: 별칭과 effort 목록), `source`, `quota`(창마다 `used_percent`·`resets_at`·`scope`·`observed_at`·`source`; 확인 못 하면 `unknown`), note("카탈로그에 있어도 계정이 못 쓸 수 있다 — 첫 턴의 400이 알린다") |
| `schedule` | 원본 claude `schedule --help`의 공개 인자 그대로 + 새 예약을 정의할 때 **`--engine claude`·`--model`·`--effort` 필수**(지금 엔진 선택지는 claude 하나 — 기본 엔진 없음 원칙과 나중의 확장을 위해; 같은 `--name`으로 바꿀 때는 기록을 잇고, `--list`에는 필요 없다). `schedule`·`unschedule`은 원본처럼 `--runs-dir`를 받지 않는다(예약의 Run/Finish가 프로젝트 기본 레지스트리를 다시 계산하므로) | 원본과 같은 모양 + 새 cli 경로 |
| `unschedule` | `--name` | 원본과 같은 모양 |
| `doctor` | `[--engine E]` | JSON 한 줄: 엔진마다 CLI 버전·로그인(codex `CODEX_HOME`), 샌드박스 전제, 한도, 레지스트리, 설정이 프로필을 넓히는 곳, 예약 준비(runner·launchd). blocker면 exit 3 |

숨은 명령: `__supervise`(런 감독), `__scheduled run|finish`(예약 잡 — GitHub Actions가 설치된 cli로 부르는 스킬의 런타임이라 하위 명령으로 둔다).

**각 명령이 자리를 얻은 이유 (Model over machinery)**: `start`·`resume`·`batch`·`stop`·`send` — 분리된 프로세스와 그 stdin·프로세스 그룹에 닿는 것은 모델의 도구가 못 한다(reach). `result --wait`·`status` — 이벤트 스트림 해석·답 확정·attention 판정은 매번 같고 events.jsonl은 읽기엔 크다. `log` — 원 이벤트는 크고, 압축 보기는 매번 같은 일. `clean` — worktree·잠금·브랜치·남은 파일 판단이 여러 곳을 일관되게 바꿔야 한다. `models` — codex 카탈로그는 모델마다 `base_instructions`가 들어 있어 읽기엔 너무 크고, 수천 개 rollout 중 최신 `rate_limits`를 찾아 범위를 맞추는 일은 틀리기 쉽다. `doctor` — 여러 시스템의 상태를 한 번에. `schedule` — GitHub 워크플로·launchd·runner를 일관되게 써야 하고, `__scheduled`는 아무도 없는 러너에서 돈다. 인계는 명령이 아니다(위 결정).

설계 판단(클로드):

- **권한 이름 `read-only|write|full`을 두 엔진에 공통**으로: 섞인 batch의 그룹 기본값(read-only·write)이 두 엔진에 유효해야 해서. read-only = codex 권한 프로필(TMPDIR·`~/.cache`만 쓰기, 네트워크 없음; 안 되면 legacy `strict` + `read_only_note`) / claude `dontAsk` + Edit 금지 + 샌드박스. write = codex `workspace-write` / claude `acceptEdits` + 샌드박스. full = codex `danger-full-access`(샌드박스 없음) / claude `auto`(스킬이 샌드박스를 더하지 않고 분류기에 맡김). `--help`는 엔진마다 실제 경계를 말하고, 응답은 엔진별 필드로 보장(scratch/strict 사유, `profile_exceptions`, `mode_mismatch`)을 잃지 않는다.
- **그룹 슬롯은 run id로 고정**: 표시·대기·그룹 stop은 고정 슬롯. 다음 라운드는 새 batch(`kind: resume`). 매니페스트 세대 + 그룹 잠금으로 갱신, 그룹 대기는 시작 때의 세대에 고정(codex `observe/follow.py:24`).
- **worktree는 엔진별 방식을 유지**하되 한 인터페이스 뒤에: claude 멤버는 Claude Code `--worktree`(`worktree.baseRef: head`, `.worktreeinclude` 복사 — 그래서 "무시된 파일은 없다"는 claude 멤버에선 `.worktreeinclude`가 복사한 것을 빼고), codex 멤버는 스킬이 자른 detached checkout. 응답 필드(`worktree.path`, `branch`(없으면 null), `base`)는 공통. **`clean`의 삭제 판단**은 저장소에 등록된 실제 worktree와 그 경로 안에 cwd가 있는 **모든 엔진의 모든 런**(그룹 밖 포함)을 본다; 런 생성(start·resume, `--cwd`가 그 worktree 안인 경우 포함)의 점유 등록과 `clean`의 제거는 같은 잠금으로 직렬화한다; `--force`도 살아 있거나 orphan이거나 meta를 못 읽는 점유자를 넘지 못한다. 점유 검사는 그 프로젝트 레지스트리의 런만 본다 — 다른 `--runs-dir`로 시작한 런은 보이지 않는다는 것을 `clean --help`가 말한다. claude의 복사본·시작 산출물·커밋 보존 규칙과 codex의 중단된 checkout 복구를 각각 테스트한다.
- **attention과 대기**: 공통 `stalled`(이벤트 없음 600초, 대기 시작부터 잼 — 원본 claude), claude는 `retrying`·`rate_limited`·`finishing`·`possibly_blocked`도. 한도 실패는 두 엔진 모두 종료 상태 `quota_exhausted`(엔진, `resets_at` — codex는 rollout 값, 없으면 오류 문장의 시각 — 그리고 `instead`: 다른 엔진 `start`를 같은 `--cwd`로). `result --wait`의 헤더 `returned_because`는 `ended | attention | member_failed | timeout` 중 하나다. 그룹 `--wait`는 모두 끝났거나, 어느 멤버에 새 attention이 생겼거나, 어느 멤버가 실패로 끝났을 때 돌아온다(원본 claude 대기는 종료 멤버를 새 attention으로 보지 않았다 — 섞인 그룹에서 한도 실패가 늦게 전달되지 않게 바꾼다). 재대기는 이미 보고한 attention을 다시 울리지 않는다. 예약의 대기는 attention을 기록하며 실제 종료까지 계속한다.
- **관측의 신선함**: codex 관측은 같은 `CODEX_HOME`의 rollout에서 온 것이고 그 창의 `resets_at`이 아직 미래일 때만 유효하다(다른 `CODEX_HOME`·지난 창은 버림). claude 관측은 레지스트리의 `rate_limit_event`가 10분 안이고 범위를 알 수 있을 때만 쓰고, 아니면 `/usage`를 부른다.
- **한도 사전 거부(두 엔진, `start`·`resume`·`batch`에만 — 예약 런에는 적용하지 않음, 단계 6)**: 요청한 모델이 쓰는 한도 범위에서, 신선한 관측이 소진(`used_percent >= 100` 또는 `rejected`)이고 `resets_at`이 미래일 때만 시작 전에 exit 1(`error: quota_exhausted`, `resets_at`, `observed_at`, `instead`: 같은 프롬프트로 다른 엔진 `start`). 관측: codex — 가장 최근 rollout의 `rate_limits`(`limit_id`↔모델 대응을 단계 5에서 실측으로 정하고, 모르면 `unknown`), `account/rateLimits/read`가 일회성 호출로 인증되어 쓸 수 있으면 그것을 우선(단계 5 프로브); claude — 레지스트리의 최근 `rate_limit_event`, 10분보다 오래됐으면 `/usage`. 오래된 관측·다른 범위·조회 실패는 `unknown`이고 거부하지 않는다.
- **모델 검증**: 새로 고른 값만 검사한다 — codex는 카탈로그에 없으면 exit 2(카탈로그를 못 읽으면 통과), claude는 `claude-` 접두 전체 id 또는 `/model` 목록의 별칭이 아니면 exit 2(못 읽으면 통과, init 실패가 알림). resume이 물려받은 모델은 카탈로그에서 빠졌어도 막지 않는다(원본 codex `runs/settings.py:12`). effort는 codex는 그 모델의 `supported_reasoning_levels`, claude는 CLI가 받는 값.

### 디렉터리 트리 (skill-maker 「Code the skill bundles」)

```
SubAgent-in-Claude/                         (레포 = 플러그인 루트)
├── .claude-plugin/{plugin.json, marketplace.json}   # 플러그인 delegate("skills": ["./.claude/skills/"]), 마켓 subagent-in-claude
├── .claude/
│   ├── plans/                              # 이 계획 + 병합 지도·라우팅 증거 (PR ①에 커밋)
│   └── skills/delegate/                    ← ~/.claude/skills/delegate 심링크 대상
│       ├── SKILL.md                        # 한 파일, 영어
│       └── scripts/
│           ├── cli.py                      # 유일한 진입점(PEP 723: requires-python, 의존성 없음). 파서·help·dispatch·종료 코드만
│           └── delegate/                   # 유일한 패키지(stdlib와 안 겹침). __init__.py 독스트링 = 트리 지도
│               ├── engine/      # feature: 엔진 인터페이스(위 계약). runs·batch·observe·schedule·models·doctor가 이것으로 엔진을 다룬다 — 기능 위에 지은 기능, 그 간선을 구조 테스트가 이름으로 허용
│               ├── runs/        # feature: start·resume·send·stop, 런 생성과 분리 supervisor
│               ├── batch/       # feature: 그룹·슬롯·worktree 준비·clean
│               ├── observe/     # feature: status·log·result·wait·attention
│               ├── schedule/    # feature: schedule·unschedule·__scheduled
│               ├── models.py    # feature: models
│               ├── doctor.py    # feature: doctor
│               ├── claude_cli/  # system: argv, stream-json, auth·버전, /usage, /model
│               ├── codex_cli/   # system: argv, 이벤트, config.toml·CODEX_HOME, 카탈로그, 로그인, rollout rate_limits
│               ├── git/         # system: 저장소·본 체크아웃 해석(서브모듈 포함)·worktree
│               ├── github/      # system: gh API, 워크플로 YAML·spec(옛 claude 헤더·spec 읽기 포함), runner, job
│               ├── launchd/     # system: plist·dispatcher
│               ├── registry/    # store: .delegate-runs/의 runs·groups·locks·inbox·schedules
│               ├── errors.py    # helper: Refusal
│               └── util.py      # helper
├── tests/                                  # unittest. support/{fake_claude, fake_codex, fake_gh, fake_launchctl, fixtures, harness.py}; smoke_real_delegate.py, smoke_real_schedule.py; e2e/{run_e2e.py, scenarios.md}
├── migrate_schedules/                      # 유지보수 잡: 옛 claude 스킬 예약을 delegate로 옮김 (스킬이 import하지 않음; 스킬 단위 인터페이스만 import)
├── README.md, CHANGELOG.md, CONTRIBUTING.md, SECURITY.md, LICENSE(Apache-2.0), CLAUDE.md, AGENTS.md
└── .gitignore                              # .codex-runs/ .claude-runs/ .delegate-runs/ .tmp/ .claude/histories .claude/worktrees/
```

- 레지스트리: 저장소 본 체크아웃의 `.delegate-runs/`(스스로 `.gitignore` `*`), git이 아니면 그 디렉터리. 런 id `YYYYMMDD-HHMMSS-<slug>-<4hex>`. meta에 `engine`, `native_id`, 엔진 태그 설정, 공통 수명주기·타임스탬프(epoch).
- 구조 테스트: 두 최상위 이름(`cli.py`, `delegate/`), import 방향(features → systems/stores → helpers; `engine`을 포함한 기능 간 간선은 이름으로), 단위 인터페이스 밖 접근 금지(테스트 포함), `sys.path` 편집 금지, 스킬 폴더에 SKILL.md·scripts만.
- 레포 위생(PR ①): `git rm --cached .claude/histories ".tmp/claude in claude" ".tmp/codex in claude"`(로컬 링크는 남기고 무시), `.claude/skills/subagent/` 삭제, 위 `.gitignore`.

### SKILL.md 구조와 각 판단의 근거

프런트매터: `name: delegate`; `description`(영어) = 무엇을(두 엔진 — GPT through the local Codex CLI, Claude through headless Claude Code — 의 백그라운드 작업자를 서브에이전트처럼: 핸들, 진행, 조종(Claude), 멈춤·이어 시키기, 섞인 그룹, 텍스트/스키마 결과, 지금 쓸 수 있는 모델과 한도, Claude 작업자 예약) / 언제(일을 GPT나 다른 Claude 세션에 넘길 때, 독립된 다른 모델의 검토·검증, 병렬, 내가 다른 일을 하는 동안, 정한 시각에) / 트리거(delegate, 위임, 작업자, codex, 코덱스, 코덱스로, 코덱스한테, GPT한테, GPT에게 시켜, claude worker, 클로드 작업자, 클로드한테 시켜, 백그라운드, 병렬로, 교차 검증, 예약 작업자, 매일 ~시에 돌려, 정기 실행) / 아닌 것(Claude Code 내장 Agent 도구·서브에이전트·에이전트 팀·`claude --bg`·Claude Code 자체 클라우드/데스크톱 예약과 /loop, Codex Cloud·`codex mcp-server`·`app-server`, GPT·OpenAI·Claude API 일반 질문). 다른 스킬 이름은 쓰지 않는다. `allowed-tools: Bash(uv run "${CLAUDE_SKILL_DIR}/scripts/cli.py" *)`.

| 절 | 담을 판단 | 자리를 얻은 속성 | 근거 |
|---|---|---|---|
| (머리) | 한 줄 호출 형식이어야 사전 승인이 맞는다(변수·`$(…)`·파이프·`\` 줄잇기는 매번 물음); 응답은 출력된 그대로 읽힌다; `<command> --help`가 계약을 가진다 | Interface over document(인터페이스가 못 하는 말만) | 두 원본 SKILL.md 첫 단락 |
| Choosing the engine | 대조 실행의 사실을 **구현 뒤 실측한 지금 값으로**: 다른 계열은 독립된 읽기를 준다 / Claude 작업자는 사용자의 Claude Code 환경(CLAUDE.md·Skill 도구로 부르는 스킬·훅·MCP·메모리)을 물려받되 스킬이 막는 도구·부모 세션 변수는 빼고 프로필 아래에서 / 모델은 `models`의 카탈로그·별칭에서 고르고, 계정에서 쓸 수 있는지는 첫 턴이 알린다 / GPT 작업자는 무엇을 보고 무엇을 못 보는지(Skill 도구 없음은 확인됨; config.toml·MCP·플러그인·훅·codex 스킬·`AGENTS.md`의 정확한 경계는 PR ②에서 실측해 그 결과만 쓴다) / 한도 풀은 둘이고 Claude 풀은 너 자신과 같다 / 턴 중 조종과 예약은 Claude만 / 새 시작은 엔진과 모델을 고르고, `models`가 지금 쓸 수 있는 모델(설명 포함)과 한도를 보인다. 순위·기본값 없음 | Colleague over autopilot(못 보는 사실), Principle over rail | 대조 실행, 성진 결정, 라우팅 증거 |
| Handing work over | 프로필이 할 수 있는 일과 가져올 증거를 정한다(read-only도 테스트를 돌린다 — 두 엔진; codex `read_only: strict`면 note가 이유); 엔진별 `full`의 실제 의미; Claude `write`는 `.claude/skills/`를 바꾸지 못한다; 환경은 프롬프트에 안 쓴 입력이다; git은 호출자 몫; `resume`(같은 이해를 이어 쓸 때) vs 새 `start`; **다른 엔진이 이어받을 때**: 대화는 넘어가지 않는다 — 이전 런이 끝났는지(살아 있으면 멈추고) 확인한 뒤 같은 디렉터리에서 새로 시작하고, 이전 작업자가 왜 멈췄는지·마지막 말·남은 변경은 검증 안 된 draft라는 것을 알리고, 브리프는 그대로; 검증 루프는 작업이 바닥낼 수 있는 술어에서 멈춘다; 내 도구 대응표(`Agent`→start+next, `Workflow parallel()`→batch, 다음 단계→`kind: resume` batch, 실행 중 말 걸기→send(Claude만), 팀원끼리 대화→없음, 라운드로) | Principle over rail, Colleague | 두 원본, 프로브 `probe-write-c282`·`r11-95d4`, 실제 인계 4건 |
| Several runs at once | 격리는 시작할 때 정한다; worktree는 커밋된 스냅숏(미커밋·무시된 파일 없음 — Claude 멤버는 `.worktreeinclude`가 복사한 것만 예외, `missing_ignored`); 섞인 그룹은 한 이름으로 보고·모으고·멈춘다; N개는 한도를 N번 쓴다(엔진별 풀); 그룹은 세션보다 오래 산다 — 이름이 유일하게 다시 못 만드는 것 | Colleague(비용·되돌리기) | 두 원본 |
| Waiting, steering and collecting | 분리된 런은 아무것도 알리지 않는다: `next.command`를 백그라운드로, 기다림을 메우려 일을 만들지 않는다; 일찍 돌아온 대기는 결정 지점(Claude: send·stop, GPT: 기다리거나 stop 뒤 resume); 읽은 결과는 확인한 결과가 아니다(`permission_denials`, `files_edited`는 셸 쓰기를 놓침); 유일한 턴이면 전경 + `--wait-timeout` | Colleague | 두 원본 |
| When a limit runs out | `quota_exhausted`는 초기화 시각과 다른 엔진을 준다; 엔진을 바꾸면 모델 계열이 바뀌고 그것이 교차 검증의 요점이었다면 의미가 바뀐다 — 바꿀지는 일에 따라 고르고 한 줄로 말한다; 기다림도 비용이다 | Colleague(사람이 결과에서 못 보는 것) | 성진 결정(한도), 라우팅 증거 §A |
| Scheduling a worker | 회차마다 아무도 안 보는 동안 구독 한도를 쓴다 — 빈도·`--timeout`은 사람이 정할 비용; 공개 레포 `--public-ok`, runner 로그인 방식은 소유자 선택 | Colleague(되돌리기 어려움) | 원본 claude |
| When something goes wrong | `doctor`; `status --run`의 `error`·`stderr_tail`; codex 인증은 `CODEX_HOME` 비교; 안을 보려면 codex rollout(`turn_context`) / `claude --resume <session_id>`; 예약 실패는 summary 첫 alert → 잡 로그 → runner; `uv`가 없으면 설치가 답 | Interface 밖 남는 것 | 두 원본 |

SKILL.md에 넣지 않는 것: 모델 이름, 엔진 순위·기본값, "번역·분석은 직접"(프로젝트 선호), 같은 판정자·한도 풀 나눠 쓰기·비례성(대조에서 기본 클로드가 이미 함), flag 목록(help 소유), 출처·실측 이력(커밋·PR 본문).

### 행동 보존 표 (원본 테스트 → 새 스위트)

새 코드의 테스트는 원본 시나리오의 **행동**을 새 인터페이스로 옮긴 것이다. 기대값은 원본 픽스처(실캡처)에서. 원본 SHA claude `7337323`, codex `e8461a1`. 각 PR은 담당 파일의 시나리오를 옮기고 **유지·의도적 변경·폐기(이유)**를 PR 본문에 적는다. 이 환경에서 재현 안 되는 경합(병합 지도: claude 시작 중 stop 두 건이 `pgrep: Cannot get process list`로 미검증)은 "미검증"으로 표시하고 본 맥에서 돌린다.

| 단계 | claude 원본 | codex 원본 |
|---|---|---|
| 1 | test_stream, test_argv, test_run_lifecycle, test_resume, test_stop, test_races, test_registry, test_observe, test_settings, test_doctor, test_install, test_cli_contract, test_structure, test_support | — (공통 계층의 codex 몫은 2) |
| 2 | — | test_argv, test_events, test_config, test_catalog, test_run_lifecycle, test_resume, test_observe, test_settings, test_doctor, test_install, test_registry_location, test_registry_races, test_cli_contract, test_structure |
| 3 | test_batch, test_clean, test_git | test_batch, test_worktree(중단된 `worktree add` 복구) |
| 4 | test_send, test_observe(attention 부분) | (stall 픽스처 신규) |
| 5 | test_doctor(quota 부분) | test_catalog(models 부분) |
| 6 | test_schedule_define, test_scheduled_run, test_schedule_state, test_summary, test_workflow, test_launchd, test_github_api, test_github_job, test_times | — |
| 스모크 | smoke_real_claude, smoke_real_schedule | smoke_real_codex |
| S6 | tests/e2e | tests/e2e |
| 폐기 | — | legacy_registry(옛 레지스트리를 읽지 않는 결정) |

릴리스 게이트 시나리오(단계 3·6·8에서): 섞인 그룹 통합(두 가짜 CLI, 한 그룹, 엔진마다 native 핸들, 시작 실패 슬롯, codex가 살아 있는 동안 claude attention, 같은 멤버에 result·stop·clean, 한 멤버 resume 뒤 옛 그룹 조회 불변), `clean` 대 같은 worktree에 `--cwd`로 시작하는 런의 경합, 그룹 이름 재사용 세대, 큐가 남은 예약 전환, 이행 부분 적용 뒤 재실행·롤백.

## 단계 (PR 하나 = 이유 하나)

각 PR은 `main`에서 판 브랜치, 커밋은 결정 하나에 하나(본문에 결정·이유·버린 대안, 가져온 지식은 원본 SHA·경로), `gh pr merge --squash`. 각 PR은 SKILL.md에서 그 PR이 거짓으로 만드는 문장을 함께 고친다. PR마다 `codex` 리뷰(read-only, 새 스레드); Claude Code 환경과 맞물리는 PR(단계 6·7·8)은 `claude` 작업자 리뷰도. 아래 번호 N은 N번째 PR이다(본문의 "PR ①"은 단계 1). **전환 뒤에는 심링크가 이 레포의 본 체크아웃을 가리키고 예약이 그 코드를 돌므로, 브랜치 작업은 worktree에서 하고 본 체크아웃은 `main`에 둔다.**

1. **feat: 레지스트리·런 엔진과 claude 드라이버** — 레포 위생, 계획·근거 파일 커밋, `engine/` 계약, `start/resume/status/log/result/stop/doctor`(claude), `--engine`·`--model` 필수, 재귀 금지, 구조 테스트. 프로브: claude `auto`의 `.claude/skills/` 경계; 옛 claude worktree 세션(레지스트리 밖)을 resume할 때 `--cwd`에 worktree와 원래 실행 디렉터리(`launch_cwd`) 중 무엇을 줘야 이어지는지(원본은 `launch_cwd`에서 띄우고 Claude가 다시 들어간다 — help에 그 답을 쓴다); 미등록 claude 세션 resume에서 effort가 복원되는지. 완료 판정: 행동 보존 표 ①열이 가짜 `claude`로 통과.
2. **feat: codex 드라이버** — 같은 명령이 codex로; read-only scratch/strict, 격리, 카탈로그 검증, `--priority`, 미등록 thread resume 계약. 프로브: 격리 런이 보는 것(config.toml·MCP·플러그인·훅·`~/.codex` 스킬·전역/프로젝트 `AGENTS.md`)을 실측해 SKILL.md 사실을 그 결과로. 완료 판정: 표 ②열이 가짜 `codex`로 통과 + 프로브 결과가 PR 본문에.
3. **feat: 섞인 그룹** — `batch`(엔진별 태스크, `full` 그룹 기본 금지), 슬롯 고정·세대, 엔진별 worktree, overlaps, `clean`(모든 엔진 점유 검사·같은 잠금·`--force` 한계). 완료 판정: 표 3행 + 섞인 그룹 통합 테스트(attention·한도 실패를 뺀 부분: 한 그룹, 엔진마다 native 핸들, 시작 실패 슬롯, 같은 멤버에 result·stop·clean, 한 멤버 resume 뒤 옛 그룹 조회 불변) + clean 경합 테스트 통과.
4. **feat: send와 두 엔진의 attention** — claude `send`·`--interrupt`, codex `send` 거부+instead, codex `stalled`, `returned_because`, 그룹 대기의 멤버 실패 반환. 완료 판정: 표 4행 + codex stall 픽스처 + 섞인 그룹에서 codex가 살아 있는 동안 claude attention으로 돌아옴 + 멤버가 실패로 끝나면(픽스처: 일반 실패) 다른 멤버 실행 중 `returned_because: member_failed`로 돌아오고, `next`로 다시 기다리면 이미 보고한 실패·attention으로는 돌아오지 않음(`next`가 본 것의 표식을 싣는다).
5. **feat: 모델과 한도** (`models`, `doctor` 한도, `quota_exhausted`, 두 엔진 사전 거부) — 프로브: codex `limit_id`↔모델 대응, `account/rateLimits/read` 일회성 호출 가능성. 완료 판정: rollout `rate_limits`(codex·premium)·`/usage`·`/model` 픽스처로 출력 테스트, 카탈로그 밖 새 모델 exit 2·물려받은 모델은 통과, 같은 범위 소진 픽스처에서 exit 1 + `resets_at`·`instead`, 다른 범위·오래된 관측은 `unknown`으로 시작, 섞인 그룹에서 codex 한도 실패(`sketch-r3-823f` 형식 픽스처)가 `quota_exhausted`와 `member_failed`로 즉시 돌아옴.
6. **feat: Claude 작업자 예약** (`schedule --engine claude`, `unschedule`, `__scheduled`; 새 헤더·spec·env 이름·`concurrency.group: delegate-<name>`·leftover ref `refs/delegate/leftovers/*`; 옛 claude 헤더·spec 읽기; `keep_definition`) — 완료 판정: 표 6행이 가짜 `gh`·`launchctl`로 통과 + **`smoke_real_schedule.py`를 전용 private 테스트 레포(위 결정)에서 실제로 통과**(원본 CONTRIBUTING의 방식; `DELEGATE_REAL=1 DELEGATE_REAL_GITHUB=1 DELEGATE_REAL_SCHEDULE_PROJECT=<checkout>`), 일회성 Finish가 비활성화까지 하는지 포함. 이 단계의 코드에 넣을 것: dispatch 입력 `slot` — 복구 디스패치는 **정확한 slot 키**로 잠금·중복 검사를 하고, 그 slot이 상태에 미실행(이행 기록의 복구 요청 포함)으로 있으면 원본의 "최신 slot만"(`registry/schedules.py:117`)·일회성 26시간 만료(`execute.py:177`) 규칙에 막히지 않으며, 일회성 복구도 자동 실행으로 기록되어 Finish가 비활성화한다; 일회성 비활성화 실패는 복구 상태로 남기고 `__scheduled finish`를 다시 돌리면 작업자 재실행 없이 종료 처리만 한다(멱등); `doctor`는 "완료 기록이 있는데 아직 활성이거나 dispatcher가 남은 일회성"을 찾아 보고한다(`instead`: `unschedule --name`). **한도 사전 거부는 예약 런(`__scheduled run`)에는 적용하지 않는다** — 예약 작업자는 그냥 시작하고, 한도에 막히면 원본의 실패 경로(read-only 1회 재시도, summary alert)를 탄다(한도로 거부된 slot이 "선점됐지만 미완료"로 남는 경우를 없애려고). 각각 가짜 `gh`로 테스트.
7. **docs: SKILL.md 전문** — 위 구조로, PR ①~⑥의 프로브 결과로 사실을 채운다. 완료 판정: 아래 검증의 SKILL.md 항목 전부.
8. **chore: 예약 이행 잡** (`migrate_schedules/`) — 아래 "전환 3"의 절차를 코드로. 완료 판정: 가짜 `gh`·`launchctl`로 dry-run 보고, 얼림·상태 이행·게시·재개, 부분 실패 뒤 재실행, 롤백 테스트 통과; 일회성 이행본의 Finish가 비활성화함을 테스트.
9. **chore(release): v0.1.0** — README(두 엔진, 설치, 요구 버전), CHANGELOG, CONTRIBUTING(스위트·스모크·S6 명령), plugin.json·marketplace.json, 태그 `v0.1.0` + GitHub 릴리스. 완료 판정: `gh release view v0.1.0` 성공 + 태그에서 받은 사본을 플러그인으로 설치한 깨끗한 세션(`claude -p`)에서 `delegate`가 스킬 목록에 보이고, `start`→`next.command`(백그라운드)→결과 회수가 사전 승인 패턴과 맞아 권한을 묻지 않음.
## 전환 (레포 밖, 릴리스 뒤 순서대로)

`~/.claude` 변경은 그 레포(원격 `tjdwls101010/Agentic-Coding`)에서 전역 Git 작업 흐름대로 브랜치·PR·스쿼시 머지한다. 이미 있는 무관한 변경(` M settings.json`의 Orca 훅, `?? skills/sns`)은 커밋하지 않고 hunk로 나눈다. PR은 이유별로 둘: **A** "feat: delegate를 위임 경로로 등록한다"(1·2), **B** "chore: 옛 claude 스킬 링크를 지운다"(6).

1. **설치(PR A)**: `ln -s "/Users/seongjin/Coding/subagent in claude/.claude/skills/delegate" ~/.claude/skills/delegate`; `settings.json` allow에 `Skill(delegate)`, `Bash(uv run "/Users/seongjin/.claude/skills/delegate/scripts/cli.py" *)` 추가; `~/.claude/skills/codex` 심링크와 `Skill(codex)`·codex `cli.py` 허용 줄 삭제(codex엔 예약이 없다; 그 전에 `~/Coding/*`·Obsidian의 옛 `.codex-runs`에 살아 있는 런이 없는지 확인); `README.md` 심링크 표 갱신. `~/.claude/skills/claude`와 그 허용 줄은 예약이 기대므로 6까지 남긴다(그동안 스킬 목록에 `claude`와 `delegate`가 함께 보인다).
2. **문장(PR A, 초안을 성진에게 보이고 승인 뒤)** — 원칙: "위임·검증·팬아웃은 내장 `Agent`·`Workflow`보다 이것"과 그 이유는 남기고, 엔진 기본값·순위는 없앤다.
   - `~/.claude/CLAUDE.md` 37·38행 → 한 항목. 초안: "- `delegate`: 위임·검증, 그리고 관점 여럿을 동시에 거는 팬아웃의 기본값은 `Agent`·`Workflow`가 아니라 이것이다. 작업자의 진행·결과·한도를 내가 들여다보고 멈추고 이어 시킬 수 있고, 구독도 이미 값을 치렀다. GPT와 Claude 중 어디에 맡길지는 일을 보고 고른다."
   - `~/.claude/hooks/plan_mode_brief.md` 9행 "codex·claude 작업자 실행" → "`delegate` 작업자(GPT·Claude) 실행"; 11행 검토자를 "`delegate`의 GPT 작업자와 Claude 작업자 가운데 계획에 맞는 쪽, 또는 둘 다"로, "CLAUDE.md의 '위임·검증 기본값은 codex'를 이 판단이 대신한다" 문장은 삭제(두 엔진 강점 설명은 유지). 고친 뒤 `python3 -m unittest discover -s ~/.claude/hooks/tests` 통과.
   - `~/.claude/skills/coding/SKILL.md:68` "through the `codex` skill" → "through the `delegate` skill's GPT worker"(다른 모델 계열이라는 취지 유지).
3. **예약 이행** (`migrate_schedules/`; 두 리뷰의 순서). 
   - 3-0 **카나리 먼저**: 새 스킬로 Obsidian에 일회성 예약 하나(`--at` 몇 분 뒤, read-only, 스모크에서 계정 사용 가능이 확인된 Claude 모델, 사소한 프롬프트)를 건다(새 코드는 다른 label 접두·레지스트리·파일 이름이라 옛 예약과 공존). 판정은 잡 색이 아니라 **기대 slot에서 작업자가 실제로 한 번 완료했고, 원격 워크플로가 비활성이며 dispatcher가 지워졌는지**. 그 뒤 그 워크플로를 지운다.
   - 3-1 **dry-run 보고**: 40개마다 워크플로 파일·id·활성 상태·spec·definition·다음 발화, plist label과 `launchctl print gui/$(id -u)/<label>` 적재 여부, `state.json`, 분류(반복 / 앞으로 올 일회성 / **끝난 일회성 = 날짜가 지났고, 비활성이고, 그 slot을 작업자가 완료했다는 런·잡 기록(옛 `state.json` jobs와 그 런 meta)이 있는 것**만 / 그 밖의 것 — 날짜가 지났는데 활성, 날짜가 남았는데 비활성, 완료 기록 없음 — 은 손대지 않고 보고), 파일 SHA, 게시 경로(기본 브랜치 보호 여부 — 보호되어 직접 쓰기가 막히면 Obsidian의 **별도 clone/worktree**에서 전체 파일을 담은 PR을 미리 준비해 두고 얼림 창에서는 머지만 한다), Obsidian의 leftover worktree와 `refs/claude/leftovers/*` 목록, 그리고 spec `prompt`·Obsidian의 CLAUDE.md·스킬에서 옛 스킬(`/codex`·`/claude`·옛 cli 경로)을 **호출에 필요로 하는** 참조(있으면 그 예약의 이행을 보류하고 성진에게). 성진에게 보고.
   - 3-2 **창과 마감**: 어느 slot도 ±60분 안에 없는 시간에 시작하고, **다음 발화 시각 전에 재개(3-6) 또는 롤백을 마칠 마감**을 둔다. 배치로 나눈다: 처음엔 반복 하나(`매일_PORTFOLIO_매도조건점검`)만 옮겨 기대 slot에서 작업자가 한 번 완료하는 것을 본 뒤 나머지. **배치마다 얼림 직전에 인벤토리를 다시 읽어** 원래 활성 상태와 파일 SHA를 기록한다(첫 배치를 기다리는 동안 상태가 바뀔 수 있다).
   - 3-3 **얼림**: 옮길 예약의 옛 plist를 기본 설치 경로에서 `.delegate-runs/.migration/backup/`으로 **옮기고**(복사가 아니라) `launchctl bootout`; `gh workflow disable`(GitHub cron 백업을 막음). 그 뒤 workflow id로, **비활성 워크플로까지 보이게 `--all`을 붙여 페이지 전체를** 조회해 `requested`·`waiting`·`pending`·`queued`·`in_progress` 런이 모두 0이고, 옛 레지스트리에 살아 있는 예약 작업자가 없을 때까지 기다린다. 그 전엔 3-4로 넘어가지 않는다.
   - 3-4 **상태 이행**: 옛 `.claude-runs/.schedules/<name>/state.json`과 그것이 가리키는 런 meta에서 slots·결과·재시도 가능 여부·jobs를 읽어 새 형식 `.delegate-runs/.schedules/<name>/state.json`에 이행 기록으로 쓴다(옛 런 id는 legacy로 표시해 재시도 조회가 실패하지 않게). **definition은 그대로 유지**(`keep_definition`). 옛 `.claude-runs`는 옮기지 않고 그대로 둔다(복사만).
   - 3-5 **게시**: 반복·앞으로 올 일회성은 새 렌더러로 워크플로 파일을 **전체 재생성**(파일 이름과 **기존 `concurrency.group: claude-<name>`은 유지** — 새로 거는 예약만 `delegate-<name>`; 헤더·spec·두 `run:` 줄·env는 새 것)해 GitHub Contents API로 파일마다 한 커밋, 인벤토리의 파일 SHA로(원본 방식; 로컬 볼트에서는 커밋하지 않는다) — 또는 3-1에서 준비한 PR 머지. 게시된 워크플로의 `--definition`이 새 `state.json`과 같은지 대조한다. 끝난 일회성은 워크플로 파일을 API로 지우고 dispatcher도 지운다(다시 켜지 않는다).
   - 3-6 **재개**: 옮긴 워크플로를 **원래 활성 상태대로** `gh workflow enable`하고 원격 상태를 확인한 **뒤에** 새 `delegate-schedule.*` plist를 bootstrap. 마감을 넘겼으면 얼림 동안 지나간 slot을 옛·새 기록과 대조하고, 아무도 잡지 않은 slot은 **원래 slot 시각을 넣어** 같은 중복 방지 경로로 디스패치한다(`__scheduled`가 dispatch 입력 `slot`을 받게 단계 6에서 만든다 — 원본은 실행 시각의 가장 최근 slot만 고르므로 늦은 디스패치 한 번이 여러 slot을 복구하지 못한다). 처리 안 된 slot이 있는 동안 완료 판정은 거짓이다.
   - **기대 상태는 최신 slot 결과에서 계산한다**(재개·롤백·완료 판정 모두): "원래 활성 상태"는 인벤토리 시점의 값이고, 그 뒤 옛 쪽(큐 소진 중)이나 새 쪽에서 완료된 일회성은 원래 활성이었어도 **비활성 + dispatcher 제거**가 기대 상태다. 불일치는 작업자 재실행 없이 종료 처리만 복구한다(`__scheduled finish` 재실행 또는 `unschedule`).
   - **롤백**: 새 정의를 한 번이라도 활성화했으면 **항상 먼저 새 쪽을 얼리고 소진한다**(새 plist를 백업 경로로 옮겨 bootout, `gh workflow disable`, 3-3과 같은 조건으로 미종료 런 0) — 그 뒤에야 "새 쪽이 slot을 잡았는가"를 판정한다. 잡았으면 그 결과를 옛 `state.json`에 반영한다. 그다음 API 커밋을 되돌리고(옛 파일 내용 복원), 각 워크플로를 위 기대 상태대로 복원하고, 옛 plist를 원래 경로로 되돌려 bootstrap. 원복 중 지나간 slot도 3-6처럼 대조한다 — 옛 CLI는 `slot` 입력이 없어 실행 시각의 가장 최근 slot만 고르므로, 놓친 slot이 바로 그 최신 slot이면 즉시 디스패치하고, 아니면 성진에게 보고한다(옛 쪽으로는 복구할 수 없음). 옛 `.claude-runs`는 손대지 않았고 옛 심링크는 6까지 있다. 잡은 단계별로 재실행 가능(된 단계는 건너뜀).
   - leftover worktree·`refs/claude/leftovers/*`는 새 스킬이 치우지 않는다 — 3-1 목록을 성진에게 넘겨 손으로 정리하게 한다.
4. (카나리는 3-0에서 했다.)
5. **전환 완료 판정**(모두 참이어야): ① 3-1 인벤토리의 40개 **각각**이 원본 workflow id·처분(이행/삭제/보류)·다음 발화와 대조되고, 이행한 것은 새 헤더·새 cli 경로·원래 활성 상태·workflow와 state의 definition 일치·`launchctl print gui/$(id -u)/delegate-schedule.…` 적재 ② Obsidian 기본 브랜치의 어느 워크플로에도 `skills/claude/scripts/cli.py`나 옛 헤더가 없다(`gh api`로; 보류한 것은 예외로 목록에) ③ `grep -l 'claude-schedule\|skills/claude/scripts' ~/Library/LaunchAgents/*.plist`가 비었고 GUI 도메인에 옛 `claude-schedule.*` label이 없다 ④ 카나리의 작업자가 기대 slot에서 완료했고 워크플로가 비활성·dispatcher가 지워졌다 ⑤ 반복 두 개가 각각 새 경로로 기대 slot에서 작업자가 한 번 완료(`gh run view`의 잡 로그에서 경로 확인) ⑥ 처리 안 된 slot이 없다 ⑦ 끝난 일회성은 지워졌고, 보고 대상은 성진이 처리 방법을 정했다 ⑧ 옛 CLI를 필요로 하는 **활성** 워크플로가 하나도 없다(보류 항목이 활성이면 GitHub cron이 옛 경로로 발화하므로 — 하나라도 있으면 판정은 거짓이고 6으로 가지 않는다).
6. **옛 링크 삭제(PR B)**: 판정이 참이면 `~/.claude/skills/claude` 심링크와 claude `cli.py` 허용 줄을 지우고 README 표를 고친다. 그 전에 Obsidian 밖 프로젝트의 옛 `.claude-runs`·`.codex-runs`에 살아 있는 런이 없는지 확인. 끝나면 `ls ~/.claude/skills`에 `codex`·`claude`가 없고 `delegate`가 있다. `~/Coding/*/CLAUDE.md`·`AGENTS.md`·`~/Coding/*/.claude/skills/*/SKILL.md`·`~/.claude/skills/*/SKILL.md`(링크 대상 포함)·`~/.claude/projects/*/memory/*.md`·이행한 예약들의 spec `prompt`에서 `/codex`·`/claude` 스킬이나 옛 cli 경로를 가리키는 곳을 grep해 목록으로 성진에게 보고한다(다른 레포 내용이라 고치지 않는다).

## 검증 (skill-maker 「When the skill is done」을 명령과 기준으로)

- **스위트**: 레포 루트에서 `python3 -m unittest discover -s tests`가 전부 통과(실패·에러 0). 구조 테스트·CLI 계약 테스트(각 `<command> --help`가 인자·출력·실패를 스스로 말하고 help와 동작이 맞음)·행동 보존 표의 모든 열·릴리스 게이트 시나리오 포함.
- **실 스모크**: `tests/`에서 `DELEGATE_REAL=1 DELEGATE_SMOKE_CLAUDE_MODEL=<계정에서 쓸 수 있음이 확인된 싼 모델> DELEGATE_SMOKE_CODEX_MODEL=<같음> python3 -m unittest smoke_real_delegate` — 두 엔진 각각 start→result --wait→resume, read-only가 테스트를 실행함, 섞인 batch→result --group→clean, codex 한도 시뮬 대신 실제 실패 픽스처는 스위트에서, 다른 엔진으로 같은 `--cwd` 이어받기, claude `send`, stop. 예약: `smoke_real_schedule`(단계 6). 결과 수치를 PR 본문 `## 검증`에.
- **플러그인 검증**: `claude plugin validate --strict "/Users/seongjin/Coding/subagent in claude/.claude/skills"`와 `claude plugin validate --strict "/Users/seongjin/Coding/subagent in claude"`가 exit 0. 단계 9의 설치 확인.
- **SKILL.md**: ① 성진에게 보이기 전에 `codex` 리뷰(read-only, 새 스레드)가 skill-maker 여섯 속성으로 검토하고 클로드가 가려 반영(그대로 채택하지 않음) ② 헤드리스 대조 S6(`python3 tests/e2e/run_e2e.py --out .tmp/e2e`): 위 12개 상황을, 사실 문단 없이 **새 SKILL.md만** 가진 opus로 다시 돌려 성진이 받아들인 기준 선택과 같은 쪽으로 나누는지(사실은 이제 SKILL.md가 실측값으로 준다; 위 원문 프롬프트는 기준선을 만든 입력이다) + 호출 모양 시나리오 3개(단일 위임과 대기, 섞인 교차 검토 batch, 한도 소진 뒤 다른 엔진으로 이어받기)에서 `next.command`를 백그라운드로 정확히 쓰고 인계 머리말이 상황에 맞는지 — control(SKILL.md 없이 help만)과 비교 ③ 성진이 **전문을** 승인(절 하나나 침묵은 승인이 아님).
- **전환**: 위 "전환 완료 판정" 여덟 항목.
- **개발 기록**: 소스·실측·버린 대안은 커밋·PR 본문과 `.claude/plans/`에, 스킬 폴더에는 없음.

## 남은 불확실

- claude `auto`에서 `.claude/skills/` 편집 경계 — PR ① 프로브.
- 격리 codex 런이 정확히 무엇을 보는지(스킬·AGENTS.md 등) — PR ② 프로브, 결과로 SKILL.md 사실을 쓴다.
- codex `limit_id`↔모델 대응과 `account/rateLimits/read` — PR ⑤(단계 5) 프로브. 모르면 `unknown`(거부 안 함).
- codex `stalled` 600초가 긴 테스트 명령에서 잘못 울리는 비율 — 실사용 뒤에야 보임.
- GPT 작업자의 이미지 생성(격리 런에서 미확인) — SKILL.md 사실에서 뺐다.
- GitHub 원격 상태(워크플로 활성, 브랜치 보호, runner)는 두 리뷰 샌드박스에서 확인 못 했다 — 3-1 dry-run이 확인한다.
