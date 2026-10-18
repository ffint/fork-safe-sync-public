# Guarded Source Archive Synchronization

Public controller for five public source archives in the ffint account. Existing fork main branches always remain unchanged, and each fork retains an archive/original-20261016 snapshot. The latest upstream commit is archived on every daily run, independent of safe-sync review.

## Activation

The sync is *inactive* until an Actions secret named FORK_SYNC_TOKEN exists in this public controller. To enable it, create a fine-grained GitHub personal access token with **Contents: Read and write** on exactly these five repos in ffint: ARTEX, strix, Seep-Reverse-Lab, rea, apk-reverse. Configure the token as a repository Actions secret here. Review its expiry and renew it on time. Never commit or paste the token into source code.

After adding the secret, open Actions > Guarded Public Fork Updates > Run workflow. Without the token, the workflow runs offline unit tests then skips all actual updates.

## Checks

Each run verifies the exact upstream GitHub repository ID before accessing the code. After fetching the current upstream main commit, it first creates a **permanent immutable** tag `upstream-snapshots/<commit-prefix>` in the fork. A separate branch, `upstream-latest-unreviewed`, always points to the newest captured upstream source. This single branch may be changed using force-with-lease if upstream rewrites history; immutable tags protect every previous capture. Both contain **unreviewed source code**, including any upstream deletions. Neither ever modifies the fork's `main`, `archive/original-20261016`, or `safe-sync` branches.

Independently, a conservative gate rejects non-fast-forward changes, removals, renames, protected files (.github, licensing, gitlinks, etc.), binary and symlink updates, over 100 commits, over 120 changed files, and disproportionately high deletions. A rejected update is shown as **REVIEW_REQUIRED**, *not* as a failed run: upstream latest is still archived, while `safe-sync` stays unchanged. An operational error (bad token, network, Git push failure) still fails the job. Every accepted safe-sync update gets its own rollback tag `safe-sync-backup/<previous SHA prefix>` and is fast-forwarded without force.

The controller does not execute source code from upstream. Do not enable Actions on the five source-code forks unless you have separately reviewed their workflows: branches/tags carrying untrusted upstream workflow files could otherwise trigger code execution. Only the controller needs Actions.

These checks are heuristics, not a promise that accepted source is harmless.

Schedule: 19:37 UTC daily (03:37 Beijing / 04:37 Tokyo). GitHub can delay or skip scheduled runs during load, so this is not a hard real-time guarantee. The controller also makes a harmless maintenance commit after 35 days of inactivity to avoid GitHub's 60-day scheduled-workflow shutdown. This maintenance job requires GitHub Actions GITHUB_TOKEN Contents: write for this controller only. Standard GitHub-hosted Actions runners in public repos are free under current rules, but GitHub's rules may change.

This replaces the private controller ffint/fork-safe-sync, whose schedule has been retired to avoid private Actions minutes.

## Handling unavailable sources and permission failures

If an upstream repository returns 404, the scheduled job emits SOURCE_UNAVAILABLE as a GitHub Actions warning rather than a false system failure, and leaves all historical code untouched. It retries daily in case the upstream becomes available again. A missing upstream cannot produce new source snapshots; forks or lookalike mirrors must never be substituted without validating the source identity.

If GitHub rejects a snapshot branch/tag push that introduces `.github/workflows` changes, a fine-grained PAT with Contents write alone is insufficient. GitHub may require **Workflows: Read and write** on the affected repository (REA, Seep-Reverse-Lab). The controller intentionally continues to mark the job as SYNC_ERROR until archive creation really succeeds; the original code remains unaffected. Grant workflow permission only to relevant repositories where possible, and keep GitHub Actions disabled on source-code forks to avoid inadvertently running untrusted upstream workflows.
