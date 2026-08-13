# AGENTS.md

1. Read [CLAUDE.md](CLAUDE.md) — the top-level fact source. It points at the
   canonical home of everything else (`README.md`, `docs/`, `Taskfile.yml`,
   `ansible/README.md`); nothing is duplicated here.
2. Before making any change, follow the
   [`weisssrv-development` skill](.claude/skills/weisssrv-development/SKILL.md)
   and the `references/` file for the change at hand — it carries the workflow
   invariants, pre-MR gates, and change-type decision tree. Claude Code invokes
   it via the Skill tool; other agents read the files directly.
