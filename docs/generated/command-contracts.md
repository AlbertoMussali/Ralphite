# Command Contracts (Generated Snapshot)

Owners: engine, cli
Last verified against commit: 70b0c1f
Generated from:

- `src/ralphite/engine/headless_agent.py`
- `src/ralphite/cli/app.py`

Verification command:

```bash
uv run --no-sync pytest tests/engine/test_docs_knowledge_base.py -q
```

Codex sample command (builder snapshot):

```bash
codex exec --json --ephemeral --skip-git-repo-check --cd /tmp/worktree --model gpt-5.3-codex -c model_reasoning_effort="medium" -c approval_policy="never" --sandbox workspace-write TASK_PROMPT
```

Cursor sample command (builder snapshot):

```bash
agent -p --force --output-format json --model gpt-5.3-codex TASK_PROMPT
```
