# S6 scenarios

Each runs with only this skill's text: the draft SKILL.md, or a control with its frontmatter and call paragraph alone (`run_e2e.py --variant`). A session whose failure comes from a permission denial or the environment is not evidence either way; rerun it. The digest's `METRICS` line counts calls that read a reply through a pipe or parser (`piped`) or held a value in a shell variable (`shell_values`) — both say a reply was not usable as printed.

| # | Host | Prompt (abridged) | Passes when |
|---|---|---|---|
| judge | one `-p` turn, no tools | The plan's twelve contrast situations, its facts paragraph replaced by the skill's text | Each situation goes the way the baseline that 성진 accepted went (the plan's list after the contrast prompt): engine, model class, permission level |
| 1 | stream-json, stdin held open | Have GPT review src/calc.py for bugs; don't change files | `start --engine codex` with `--model` and `--effort` chosen (from `models` or `--help`), `--profile read-only`; the reply's `next.command` run in a background Bash call as given; the findings reported once it returns; no `piped` or `shell_values` |
| 2 | stream-json, as 1 | Two independent reviews of src/calc.py at once, one GPT and one Claude; compare them | one `batch` with a tasks file holding one codex and one claude line (or two `start`s), each naming model and effort, read-only; the group's `next.command` in the background; both results collected and compared |
| 3 | stream-json, as 1 (the fake codex stops at its usage limit) | Have GPT add type hints to src/util.py | after the run ends `quota_exhausted`, the session either waits (saying why) or starts a claude worker in the same directory whose prompt says where the GPT worker stopped, what it last said, and that its changes are an unverified draft, and says in a line which it chose |
