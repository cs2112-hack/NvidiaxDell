# OpenClaw for document search

Status: **the r0 side is built and verified from a real OpenShell sandbox.**
OpenClaw itself is not onboarded on this host yet (see "Before starting").

## The idea

People search the company's documents by messaging an OpenClaw agent instead of
opening the web interface. The agent calls r0's search and sends back what r0
returns. It does not search, summarise or answer on its own, and it has no
access to document generation.

```
person ──► OpenClaw agent ──► lks serve-search ──► lks.chat ──► verbatim answer with citations
```

## What was built

| Piece | File | What it does |
|---|---|---|
| Search server | `src/lks/search_api.py`, `lks serve-search` | Three GET routes, plain text, read-only: `/search?q=`, `/clause?ref=`, `/health`. Nothing else. |
| Tests | `tests/test_search_api.py` | No `/api/*` route reachable; no `inputs` or `scope` accepted; foreign Host and Origin refused; wildcard bind refused; output equals `Answer.render()`. |
| Policy preset | `openclaw/lks-search-policy.yaml` | Sandbox may reach only those three routes on `host.openshell.internal:8770`, only from `curl`. |
| Skill | `openclaw/skills/lks-search/SKILL.md` | Tells the agent to call r0 and relay the text word for word. |

`/search` returns exactly what `lks ask` prints. It takes the question alone,
so an agent cannot choose the facts a rule runs on; a rule question comes back
naming the inputs it needs. `/clause` returns exactly what `lks cite` prints.

The main web API is unchanged. It stays on loopback with its Host guard,
because some of its endpoints write.

## Why the agent cannot do more

Checked from inside an OpenShell sandbox carrying this policy:

| Request from the sandbox | Result |
|---|---|
| `curl` `/health`, `/search`, `/clause` on 8770 | answered |
| `curl` `/api/state`, `/nope` on 8770 | 403 from OpenShell (the server itself answers unknown paths with 404) |
| `curl` port 8765 (web API), port 11434 (Ollama) | 403 from OpenShell |
| `python3` to `/health` on 8770 | 403 from OpenShell (wrong binary) |

## Running it

1. Start the search server on the OpenShell bridge address (the address
   `host.openshell.internal` resolves to inside a sandbox; `172.18.0.1` on
   this host):

   ```bash
   . scripts/env.sh
   $PY scripts/lks serve-search --host 172.18.0.1 --port 8770
   ```

2. Onboard an OpenClaw sandbox against the local model:

   ```bash
   nemoclaw onboard --agent openclaw --name lks-search
   ```

3. Apply the policy, reviewing the dry run first:

   ```bash
   nemoclaw lks-search policy add --from-file openclaw/lks-search-policy.yaml --dry-run
   nemoclaw lks-search policy add --from-file openclaw/lks-search-policy.yaml --yes
   ```

4. Install the skill:

   ```bash
   nemoclaw lks-search skill install openclaw/skills/lks-search/
   ```

5. Try it without a channel, then add one:

   ```bash
   nemoclaw lks-search agent -m "What do our documents say about data retention?"
   nemoclaw lks-search channels add <channel>
   ```

## Before starting

- **Onboarding.** `docs/AGENTS.md` records that OpenClaw onboarding through
  NemoClaw previously needed NVIDIA inference credentials this host does not
  have. If that is still the case, use Hermes (`--agent hermes`); the policy
  and skill commands are the same.
- **`curl` in the agent image.** The policy allows only `/usr/bin/curl`. Check
  with `nemoclaw lks-search exec -- which curl`, and adjust the preset's
  `binaries` if the agent's image differs.
- **One local model.** The agent shares it with the rest of the system, so
  replies are slow while other jobs run. Search itself does not use the model.
