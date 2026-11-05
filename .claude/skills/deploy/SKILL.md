---
name: deploy
description: Ship Puntmore to production — merge a pull request into `main`, which Render auto-deploys; verify with /api/ready. Use when asked to deploy, ship, or release Puntmore.
---

# Deploy Puntmore (production)

There is **no deploy script and no local stack**. puntmore.com is ONE Render web
service (`render.yaml`, `autoDeploy: true`): the API image (`render/api/Dockerfile`)
builds the frontend into itself and serves it from the same origin. **Every push
to `main` is a production deploy.** Render promotes the new build only once
`healthCheckPath: /api/ready` returns 200, so a build that can't serve never
goes live. There is no staging.

Work in your own worktree on a branch of `~/Projects/puntmore`; nothing reaches
`main` except through a pull request (`docs/decisions.md`).

## Before opening the pull request

```bash
git status --short                                          # only the intended files
(cd render/api && ./.venv/bin/python -m pytest -q)          # backend suite
(cd render/frontend && npm run lint && npm run test && npm run build)
```

A frontend change must also be **looked at in a browser** first — use the `run`
skill (headless Chrome + stubbed feed, no backend needed).

## Ship

```bash
git add <files>
git commit                         # gitleaks pre-commit hook runs
git push -u origin <branch>
gh pr create --base main           # Render starts the build when the PR is merged
```

## Verify

```bash
curl -fsS https://puntmore.com/api/ready   # {"status":"ready","checks":{"database":true,"providers":true},...}
```

- The build takes a few minutes. `/api/ready` stays 200 throughout (old instance
  serves until the new one passes), so it confirms health, **not** which commit
  is live — there is no version endpoint. Confirm a change by eyeballing the
  app, or via the Render dashboard's deploy log.
- Backend only: the response shape of the changed endpoint, if it's unauthenticated.

## Rollback

```bash
git revert <sha>    # on a branch, then a pull request; merging it redeploys
```
or "Rollback to previous deploy" in the Render dashboard.

## Report back

- The pull request and the merge commit, the `/api/ready` status, and exactly what to eyeball.
- Never echo secrets: env vars live on Render (`sync: false` in `render.yaml`)
  and in the git-ignored `.env`. If a feature needs a new env var, say so —
  it's a dashboard edit, not a redeploy.
