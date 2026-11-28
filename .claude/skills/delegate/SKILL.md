---
name: delegate
description: "Start background workers on two engines — GPT through the local Codex CLI, Claude through headless Claude Code — and manage them like subagents: a handle at once, progress, steering a Claude worker mid-turn, stopping and resuming, mixed groups, text or JSON-schema results, each engine's models and usage limits, and scheduling a Claude worker on this Mac through GitHub Actions. Use when work should go to GPT or to another Claude session you can watch and collect, for an independent model's review, for workers in parallel, for a long task while you do something else, or unattended at a set time. Triggers include: delegate, 위임, 작업자, codex, 코덱스, 코덱스로, 코덱스한테, GPT한테, GPT에게 시켜, claude worker, 클로드 작업자, 클로드한테 시켜, 백그라운드, 병렬로, 교차 검증, 예약 작업자, 매일 ~시에 돌려, 정기 실행. Not for Claude Code's Agent tool, subagents or agent teams, claude --bg, Claude Code's cloud or Desktop scheduled tasks or /loop, Codex Cloud, codex mcp-server or app-server, or general questions about GPT, OpenAI or the Claude API."
allowed-tools: Bash(uv run "${CLAUDE_SKILL_DIR}/scripts/cli.py" *)
---

# Workers on two engines as managed subagents

Call it as `uv run "${CLAUDE_SKILL_DIR}/scripts/cli.py" <command> …`, written out in full on one line: the pre-approval matches the command text, so a path kept in a shell variable, a `$(…)` or a pipe added to the call, or a command continued with `\` asks for permission every time. Replies are made to be read as printed. `--help` lists the commands, and `<command> --help` owns every flag, default, refusal, output field and exit code.

## Choosing the engine

**The other family gives an independent reading.** A GPT worker is trained apart from you, so it can catch what you and your own tests agree to miss; a Claude worker is your own family and tends to read the way you do.

**What each worker brings with it is input you did not write.** A Claude worker runs in the user's whole Claude Code environment — CLAUDE.md files, the user's skills through its Skill tool, hooks, plugins, MCP servers, memory — under its profile; it cannot start or message this skill's workers, but it can start its own built-in subagents, which spend the same Claude limits. A GPT worker reads the project's and the user's `AGENTS.md` and has the skills installed for Codex, its apps and its web tools, but none of the MCP servers, plugins or hooks in the user's config.toml, and no Skill tool. Either way, say in the prompt what matters for this task.

**There are two limit pools, and the Claude pool is the one you and your built-in subagents are spending too;** every member of a group draws on its own engine's pool. `models` shows what each engine offers now — codex with a description of each model, claude as aliases, each the newest of its family — with their efforts and how much of the account's limit is left; whether the account can use a model shows on its first turn. An alias moves to its family's newest model, so when runs must stay comparable across rounds, name the full model id an earlier run recorded (`status --run`'s `model`).

**Only a Claude worker can be steered mid-turn (`send`) or scheduled.**

## Handing work over

**The profile decides what a worker can do, and so what it can show you.** `read-only` on either engine reads anything and runs tests and builds in a sandbox, so a review or an investigation can bring back what it ran, not only what it read — unless a GPT worker's reply says `read_only: strict` — but nothing can listen on localhost there unless the user's sandbox settings allow it, so a test that serves itself or a browser it starts fails before it checks anything. A Claude worker's hooks, MCP tools and web fetches run outside that sandbox under the user's own permission rules, on every profile, so `read-only` does not stop what those can do, and the user's settings can widen a profile further: the reply's `profile_exceptions` names how. `write` changes files in its directory; Claude Code keeps a Claude worker out of its own configuration under `.claude/` (skills, hooks, settings, plans) there. `full` is for the network or writes outside the directory, and the engines differ most here: a GPT worker runs with no sandbox at all, a Claude worker in auto mode, where a classifier reviews each call.

**Git is yours, as far as the prompt can make it.** Workers are told to leave commits, branches, pushes and merges to you and to report what they changed, but a Claude worker also reads the user's CLAUDE.md, which can say otherwise; after a writing run, `git status` and `git log` in its directory show what it did.

**Handing a task to the other engine does not carry the conversation.** Make sure the previous run has ended — stop it if it is live — then start fresh in the same directory with the same brief and the profile that allows the same work there — the names are shared, what they allow is not, most of all `full` — adding where the previous worker stopped, what its last message said, and that any change it left is an unverified draft.

**A verification loop stops on a predicate the work can satisfy.** "Until nothing new comes back" never ends, because each new look finds something. Stop on something the work runs out of: no finding reachable in ordinary use, a named property that holds, a `--schema` answer over a set you can bound. A reviewer reading its own list, or text it has seen before, is anchored on its earlier reading: when the text has been reworked, a fresh reviewer given the earlier findings and which you rejected and why reads it without that anchor; resuming suits checking whether particular findings were addressed, and replays a transcript that grows every turn.

| Yours | Here | Not available |
|---|---|---|
| `Agent` | `start`, then its reply's `next.command` in the background | |
| `Workflow` `parallel()` | `batch`: several `--task` on one engine, or a `--tasks-file` whose lines name their engine, for both | |
| a next stage | `batch` with a `--tasks-file` of one `kind: resume` line per member, once every member has ended | a stage that starts itself |
| a word to a running subagent | `send` to a Claude worker; `--interrupt` ends its current turn | the same to a GPT worker: wait, or stop it and resume |
| teammates messaging each other | | run rounds: collect, then resume each member with the others' findings |
| a scheduled or recurring agent | `schedule`, for a Claude worker, through this repository's runner | a scheduled GPT worker |

## Several runs at once

**Decide isolation when you start.** Members share your tree by default; when two may edit the same files, start them with `--worktree`, which gives each fresh writer without a `--cwd` of its own a checkout of your local HEAD, without your uncommitted changes or what git ignores. A resumed member stays where its session or thread ran, so resuming never isolates writers that shared your tree.

**A group outlives the session**: its name stays reserved and its checkouts stay on disk until `clean`. Collect with `result --group`, which cuts each member's message short: read a member whose `shown_bytes` is below its `bytes` with `result --run`. Moving changes out of the checkouts into your tree is yours to do.

## Waiting, steering and collecting

**A detached worker announces nothing.** Run the reply's `next.command` in a background Bash call, as given; its exit notifies you, and the background call's output is the result. Then use the turn for other work or hand it back; don't invent work to fill the wait.

**A wait that comes back before the end is a decision point.** `returned_because` says why — a member that failed, or `attention` with what the worker was doing. Wait again with its `next`, correct a Claude worker still in its turn with `send`, or `stop` it.

**A result read is not a result checked.** `permission_denials` is what a Claude worker could not do, so decide whether that needs another profile; `files_changed` misses files a shell command wrote, on either engine; `git status` and `git log` in its directory, against how you left it, show what changed.

**If this is your only turn**, run `next.command` in the foreground with `--wait-timeout` added and the Bash call's own timeout above it; if the budget runs out first, hand back the run id and say the work is unfinished.

## When a limit runs out

A run the limit ended is `quota_exhausted`, with when the limit resets when the engine said so and, as `instead`, the other engine's start in the same directory on the same profile; `start` refuses up front when a fresh reading already shows the limit used up. Switching engines switches model family: when the run was the cross-check, the other engine changes what is being checked. Choose by the work, and say in a line which you chose and why; waiting for the reset is a cost too.

## Scheduling a Claude worker

**Every run draws on the subscription's limits with nobody watching**, for as long as the schedule stands, so how often it runs and its `--timeout` are a cost the person sets; a scheduled run is not held back by a used-up limit, it starts, fails, says so in its summary and is not run again. A run that waits inside itself for a later event — a data release, a market open — spends its timeout waiting, and when the timeout passes it stops mid-work, leaving whatever it opened (a branch, a pull request) as it was: schedule it for when its input exists. **`schedule` commits the prompt to the repository's default branch without a pull request**, and it stays in git history after `unschedule`. **Some fixes are the owner's to choose**: scheduling in a public repository (`--public-ok`) lets anyone's pull request run code on this Mac, and where the reply's `runner.problems` offers more than one fix, such as how the runner's service gets the Claude login, each exposes that login differently.

## When something goes wrong

- `doctor` checks each engine's environment without starting a worker. A run that fails says why in `status --run <id>` (`error`, `stderr_tail`); `log --run` shows what it did.
- Auth that works in your terminal but not for a GPT worker: compare `doctor`'s `codex_home` with the terminal's first, since two environments resolving different `CODEX_HOME`s is the likeliest cause.
- `mode_mismatch` means a Claude worker did not start in its profile's permission mode (auto mode on a model without it): treat what it may do as unknown, or choose another model or profile.
- Beyond `log`: a GPT run's turns are in its rollout, `<doctor's codex_home>/sessions/**/rollout-*-<thread_id>.jsonl`, one `turn_context` line per turn with its model, effort, cwd and permissions; the user can reopen a Claude session with `claude --resume <native_id>` in its directory — give them that command.
- A scheduled run that ends red says why in its summary's first alert; without a summary, in the job's log, and a job no runner took fails on its run page after waiting. `Not logged in` there is the runner's service, not the worker: `doctor`'s `schedule.runners.problems` names what it can see from here.
- If the command itself is not found: without `uv` nothing here runs, and installing it is the fix; with `uv`, the skill's path did not resolve: `ls "${CLAUDE_SKILL_DIR}/scripts"`.
