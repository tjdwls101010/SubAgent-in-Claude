# Agentic Delegate

The `delegate` skill (`.claude/skills/delegate/`) of the `delegate` plugin: workers on two engines — GPT through the Codex CLI, Claude through headless Claude Code — as managed subagents.

- `SKILL.md` carries only judgment the CLI's help cannot; flags, outputs and exit codes live in `scripts/cli.py --help`, which is the contract.
- The package's map is the docstring of `scripts/delegate/__init__.py`; `tests/test_structure.py` keeps its layering. Only `engine/` imports an engine's CLI unit.
- Tests: `python3 -m unittest discover -s tests` (fake engines first on PATH). Expected values come from real captures in `tests/support/fixtures/`, never from the code under test.
- No model name (slug or alias) appears in SKILL.md, `--help` or the skill's code; `tests/test_install.py` checks it.
