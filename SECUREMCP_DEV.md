# SecureMCP development — one by one, push to xsecuremcp2.0

**Audience:** Agents and developers building SecureMCP on top of FastMCP in this repo.

This repo is **xsecuremcp2.0** (fork of fastmcp). All SecureMCP work is committed and pushed only to **origin** (PureCipher/xsecuremcp2.0). Never push to upstream (fastmcp).

---

## Workflow: one change at a time

1. **One logical change per commit**  
   Implement a single feature, fix, or refactor. Keep the diff small and reviewable.

2. **Validate before commit**
   ```bash
   uv sync
   uv run pytest -n auto
   uv run prek run --all-files   # optional; run if pre-commit is set up
   ```

3. **Commit and push to xsecuremcp2.0**
   ```bash
   git add <files>
   git commit -m "SecureMCP: <short description>"
   git push origin <branch>
   ```
   Use `main` or a feature branch (e.g. `securemcp-core`, `feature/policy-engine`). Push only to **origin**.

4. **Repeat**  
   Next task = next change → test → commit → push. Do not batch unrelated changes.

---

## Branching

- **main** — default; daily sync brings in fastmcp. Use for small, direct changes.
- **Feature branches** — e.g. `feature/<name>`. Use for larger work; merge to `main` when done, then push.

---

## Remotes (reminder)

| Remote    | Role                          | Push? |
|----------|---------------------------------|-------|
| **origin**   | PureCipher/xsecuremcp2.0 (your repo) | Yes   |
| **upstream** | jlowin/fastmcp                  | No    |
| **Hugging Face** | Mirror of xsecuremcp2 (HF repo only fetches from here) | We push to HF from CI |

---

## Sync with Hugging Face

xsecuremcp2 is synced to a Hugging Face repo so that the HF repo stays in sync by receiving pushes from this repo (fetch-only from HF’s perspective).

### Credentials

1. **Hugging Face token (write)**  
   Create a token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) with **Write** scope (or a fine-grained token with write access to the target repo).

2. **GitHub secrets and variables**  
   In this repo’s **Settings → Secrets and variables → Actions**:
   - **Secret `HF_TOKEN`** — the Hugging Face token from step 1.
   - **Variable `HF_REPO`** — the repo path on the Hub, e.g. `username/xsecuremcp2` for a model repo or `spaces/username/xsecuremcp2` for a Space.
   - **Variable `HF_USER`** (optional) — your HF username; if unset, the workflow uses `x-access-token` as the HTTP user for the token.

3. **Repo on the Hub**  
   Create the target repo on Hugging Face (e.g. a model repo or Space) so `HF_REPO` matches it.

No other credentials are required for the HF sync; the workflow uses `GITHUB_TOKEN` to push to **origin** and `HF_TOKEN` only to push to Hugging Face.

---

## For agents

When developing SecureMCP in this repo:

1. Make **one** logical change (one feature, one fix, one doc update).
2. Run tests; fix any failures.
3. Commit with a clear message (e.g. `SecureMCP: add policy engine stub`).
4. Push to **origin** only: `git push origin <current-branch>`.
5. Then proceed to the **next** change and repeat.

Do not push to `upstream`. Do not combine multiple unrelated features in one commit.
