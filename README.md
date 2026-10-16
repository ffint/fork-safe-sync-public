# Guarded Source Archive Synchronization

Public controller for five public source archives in the ffint account. Existing fork main branches always remain unchanged, and each fork retains an archive/original-20261016 snapshot. Only the safe-sync branches are eligible for guarded updates.

## Activation

The sync is *inactive* until an Actions secret named FORK_SYNC_TOKEN exists in this public controller. To enable it, create a fine-grained GitHub personal access token with **Contents: Read and write** on exactly these five repos in ffint: ARTEX, strix, Seep-Reverse-Lab, rea, apk-reverse. Configure the token as a repository Actions secret here. Review its expiry and renew it on time. Never commit or paste the token into source code.

After adding the secret, open Actions > Guarded Public Fork Updates > Run workflow. Without the token, the workflow runs offline unit tests then skips all actual updates.

## Checks

For each fork, it requires the exact upstream GitHub repository ID and main branch; rejects non-fast-forward changes, removals, renames, protected files (.github, licensing, gitlinks, etc.), binary and symlink updates, over 100 commits, over 120 changed files, and disproportionately high deletions. Every accepted update first pushes a backup tag safe-sync-backup/<previous SHA prefix> without force. Then and only then it fast-forwards safe-sync. It does not run upstream project code. This is an intentionally strict heuristic, not a security guarantee. Review failed runs before manually approving legitimate major refactors.

Schedule: 19:37 UTC daily (03:37 Beijing / 04:37 Tokyo). Scheduled workflows in inactive public repos can be disabled by GitHub after 60 days. Standard GitHub-hosted Actions runners are free in public repositories under current GitHub rules, but policy can change.

This replaces the private controller ffint/fork-safe-sync, whose schedule has been retired to avoid private Actions minutes.
