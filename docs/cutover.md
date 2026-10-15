# Cutover: v2 governs itself, and v1 is archived read-only

This is the person's checklist for rebuild item 7.6. Until it is run, v1 governs
`pragmattie/pragmattie-sync-agentic` and v2 watches in `observe`. After it, v2's agents post and
gate on this repository, v1 is stopped, and `geoffsmattie/pragmattie-sync` is archived read-only
with a dump of its database kept outside both repositories. Nothing of v1 is deleted.

Do the steps in order. Each says what to do, how to check it and how to undo it. Record each one in
the [log](#log) at the end as you go.

"v1's folder" and "v2's folder" are the two local checkouts, each with its own `.env` and Docker
Compose stack. Run v2's commands in v2's folder; `docker compose exec agents …` runs inside v2's
`agents` container, which has the mode and the token.

## 1. Before

**Do:** confirm all of these, and stop if any isn't true:

- 7.1 to 7.5 are merged.
- 7.2's latest comparison report (`python -m sdlc.compare_v1`, see
  [`v1-comparison.md`](v1-comparison.md)) has been read and accepted by Geoff.
- 7.3's import is done (`python -m sdlc.import_v1 FILE --apply`) and its counts checked against
  v1's export.
- No agent PR is open or waiting to deploy: a quiet moment. Nothing should be mid-gate while the
  two systems swap.
- v1's database is about to be dumped (step 2) **before** anything changes.

**Check:** the repository's open pull requests and the `deploy.yml` runs on `main` show nothing
in progress.

**Undo:** nothing has changed yet.

## 2. Keep v1's record

**Do:** in v1's folder, dump v1's database and keep the file outside both repositories:

```sh
docker compose exec -T db sh -c 'mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --routines pragmattie_sync' > v1-final-<date>.sql
```

Move `v1-final-<date>.sql` somewhere outside both checkouts (and back it up). Note its size and
the row counts of v1's main tables in the log, for example:

```sh
docker compose exec -T db sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "SELECT table_name, table_rows FROM information_schema.tables WHERE table_schema = \"pragmattie_sync\" ORDER BY table_name"'
```

(`table_rows` is an estimate for InnoDB; `SELECT COUNT(*)` the audit and approvals tables for exact
figures.)

**Check:** the file is not empty, ends with `-- Dump completed`, and is not inside either
repository (`git status` in both shows nothing new).

**Undo:** nothing to undo; the dump only reads.

## 3. v2's write token

**Do:** create a new fine-grained personal access token:

- Resource owner `pragmattie`; repository access **only** `pragmattie-sync-agentic`.
- **Expiry: 90 days.** Note the date in the log, and renew it before it lapses (an expired token
  stops v2's agents, and v2 fails closed).
- Repository permissions:
  - Metadata: read
  - Contents: read
  - Issues: read and write
  - Pull requests: read and write
  - **Commit statuses: read and write**
  - Deployments: read and write
  - Actions: read

Put it in v2's `.env` as `GITHUB_TOKEN`, replacing the read-only one. Never commit it, paste it
into an issue or print it.

**Check:** `docker compose up -d --force-recreate orchestrator`, then
`docker compose exec orchestrator python -m sdlc.cutover check`: "Token: repository" and "Token:
statuses" pass, and "Token: identity" names the token's owner, whose comments starting with v2's
markers v2 treats as its own. "Token: write statuses" is a WARN on purpose: it can't be tested without writing a
status, so step 5 confirms it. The mode and polling lines still fail at this point.

**Undo:** put the read-only token back as `GITHUB_TOKEN` and revoke the new one on GitHub.

## 4. Stop v1 governing this repository

**Do:** in v1's `.env`, comment out `GITHUB_TOKEN_AGENTIC`. Then recreate v1's agent only:

```sh
docker compose --profile agents up -d --no-deps --force-recreate agent
```

**Check:** v1's log (`docker compose logs --tail 50 agent` in v1's folder) no longer says "also
governing pragmattie/pragmattie-sync-agentic".

**Undo:** restore `GITHUB_TOKEN_AGENTIC` in v1's `.env` and run the same command again.

## 5. v2 takes over

**Do:** in v2's `.env`, set `ORCHESTRATOR_MODE=enforce` (and `PLANNER_MODE` as decided). Recreate
v2's `agents` service, and the `orchestrator` service so its checks and the demo reset see the
same mode and token:

```sh
docker compose --profile agents up -d --force-recreate agents orchestrator
```

**Check:**

- Within a minute, `docker compose exec orchestrator python -m sdlc.demo_check` shows
  `ORCHESTRATOR_MODE` enforce.
- The next open PR (or a test PR from a `demo-` branch) gets v2's risk comment, its `tier:` label
  and the `risk-gate` status. This is what confirms the token may write. It gets **one** risk
  comment, edited in place on the next poll; a second copy at the next poll means v2 doesn't
  recognise its own comments, so roll back (step 8).
- `docker compose exec agents python -m sdlc.cutover check` passes every line (exit code 0):
  - the agents have polled in the last 5 minutes (their newest audit row; on a quiet repository
    there may be nothing to decide, so open the test PR first);
  - for the newest open PR, v2's own audit trail has a `pr_risk` row for its head commit whose
    status was posted. Both tokens belong to the same person, so GitHub can't tell whose
    `risk-gate` it is; v2's audit trail can. A commit v2 scored in `observe` keeps its row
    (status skipped), so push a commit to that PR or use the test PR.
- A merge to `main` gets v2's `release-gate` status and deploys.

**Undo:** the rollback (step 8).

## 6. Release the archive

Only once step 5 has passed. After this step, the rollback needs v1's stack started again first.

**Do:**

- In v1's folder, stop v1's stack and keep its volume: `docker compose down` (never `-v`).
- On GitHub, archive `geoffsmattie/pragmattie-sync`: Settings → General → Danger Zone → Archive
  this repository. It becomes read-only; archiving is reversible.

**Check:** `docker compose ps` in v1's folder lists nothing running; `docker volume ls` still
lists v1's database volume; the repository on GitHub shows the "archived" banner.

**Undo:** unarchive the repository (same place, "Unarchive this repository") and
`docker compose up -d` in v1's folder. Then the rollback.

## 7. Re-record the demo baseline

**Do:** in v2, with the repository clean:

```sh
docker compose exec orchestrator python -m sdlc.demo_reset baseline --force
.\scripts\demo.ps1 check
```

Geoff applies [`ci/proposed/CLAUDE.cutover.md`](../ci/proposed/CLAUDE.cutover.md) to `CLAUDE.md`
at this step.

**Check:** the pre-demo check ends with "Ready for the demo." and shows `ORCHESTRATOR_MODE`
enforce.

**Undo:** record the baseline again (`--force` replaces it).

## 8. Rollback

Any time before step 6 (after it, undo step 6 first):

1. In v2's `.env`, set `ORCHESTRATOR_MODE=observe`; run
   `docker compose --profile agents up -d --force-recreate agents orchestrator`.
2. In v1's `.env`, restore `GITHUB_TOKEN_AGENTIC`; run
   `docker compose --profile agents up -d --no-deps --force-recreate agent` in v1's folder.
3. Optionally, put v2's read-only token back (step 3's undo).

v1 resumes where it stopped: it re-reads the open PRs. Nothing is lost, because each side's audit
trail is append-only.

**Check:** v1's log says "also governing pragmattie/pragmattie-sync-agentic" again;
`python -m sdlc.demo_check` in v2 shows observe; the next PR gets v1's risk comment.

## Log

Record each step as you do it: the dump's size and row counts (step 2), the token's expiry date
(step 3), and anything that didn't go as written.

| Date | Who | What | Result |
| --- | --- | --- | --- |
| | | | |

**v1's final dump (step 2):** file name, size, where it is kept, and the row counts.

| Table | Rows |
| --- | --- |
| | |
