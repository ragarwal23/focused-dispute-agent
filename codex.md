Invoke OpenAI Codex for its input on a question or task.

Arguments: $ARGUMENTS

## Invocation

Always use `npx -y @openai/codex` (not bare `codex` — it's not in PATH).

Run with `-o <tmpfile>` to capture only the final answer, then read and present it.

```bash
# 1. Run Codex, capture final message to a temp file
CODEX_OUT=$(mktemp /tmp/codex-XXXXXX.md)
npx -y @openai/codex exec \
  -m gpt-5.3-codex \
  -s read-only \
  -C "$(pwd)" \
  --skip-git-repo-check \
  --ephemeral \
  -c model_reasoning_effort=high \
  -c model_reasoning_summary=none \
  -o "$CODEX_OUT" \
  "PROMPT"

# 2. Read the output file with the Read tool and present the result
# 3. Clean up: rm "$CODEX_OUT"
```

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `-m <model>` | `gpt-5.3-codex` | Model selection |
| `-s <sandbox>` | `read-only` | `workspace-write` for fixes, `danger-full-access` only if user requests |
| `--full-auto` | — | Alias for `-s workspace-write` with relaxed approval. Don't combine with `-s` |
| `-c model_reasoning_effort=<level>` | `high` | Use `xhigh` only for complex stuck problems |
| `-C <dir>` | `$(pwd)` | Working directory for Codex |
| `--skip-git-repo-check` | always | Required — workspace may not be a git repo |
| `--ephemeral` | always | Don't persist session files |
| `-o <file>` | always | Capture final answer cleanly |
| `PROMPT` | — | Enrich with context from conversation (file paths, snippets, plan paths) |

## Full-auto mode (for fixes)

```bash
CODEX_OUT=$(mktemp /tmp/codex-XXXXXX.md)
npx -y @openai/codex exec \
  -m gpt-5.3-codex \
  --full-auto \
  -C "$(pwd)" \
  --skip-git-repo-check \
  --ephemeral \
  -c model_reasoning_effort=xhigh \
  -c model_reasoning_summary=none \
  -o "$CODEX_OUT" \
  "PROMPT"
```

## Resume

Extract `session id: <UUID>` from stdout after every call. Resume with:

```bash
npx -y @openai/codex exec resume <SESSION_ID> "Follow-up"
```

Never use `--last`. Never use `--json`.

## When to resume vs. new

Resume for multi-step debugging or extended discussions. New conversation when topic changes.

## Workflow

1. Build the prompt — enrich `$ARGUMENTS` with relevant file paths, code snippets, or plan references from the conversation
2. Run the command above (use `run_in_background` for Bash tool since Codex can take 30-120s)
3. When complete, read the `-o` output file with the Read tool
4. Present Codex's answer to the user, noting it came from GPT-5.3 Codex
5. Clean up the temp file
