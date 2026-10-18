#!/usr/bin/env python3
"""Fail-closed fast-forward of a fork's safe-sync branch; never execute upstream code."""
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error

class Block(Exception):
    pass

class GuardRejected(Block):
    """An expected review-needed update. Not a runtime or authentication error."""
    pass

def git(*args, ok=False):
    proc = subprocess.run(['git', *args], capture_output=True, text=True)
    if proc.returncode and not ok:
        raise Block('git ' + str(args[0]) + ': ' + proc.stderr[-500:])
    return proc

def git_raw(*args):
    proc = subprocess.run(['git', *args], capture_output=True)
    if proc.returncode:
        raise Block('git ' + str(args[0]) + ': ' + proc.stderr.decode('utf-8', 'replace')[-500:])
    return proc.stdout

def ancestor(base, head):
    return git('merge-base', '--is-ancestor', base, head, ok=True).returncode == 0

def diff_files(old, new):
    raw = git_raw('diff', '--name-status', '-z', '-M', old, new)
    if not raw or not raw.endswith(b'\x00'):
        if raw:
            raise GuardRejected('Malformed file-change list')
        return []
    parts = raw[:-1].split(b'\x00')
    out = []
    idx = 0
    while idx < len(parts):
        status = parts[idx].decode('ascii', 'strict')
        idx += 1
        if status not in ('A', 'M'):
            raise GuardRejected('File deletion/rename/type-change or unsupported status: ' + status)
        if idx >= len(parts):
            raise GuardRejected('Malformed file-change record')
        path = parts[idx].decode('utf-8', 'surrogateescape')
        idx += 1
        if not path:
            raise GuardRejected('Empty file path')
        name = path.casefold()
        basename = name.rsplit('/', 1)[-1]
        if (name.startswith('.github/') or
            name in ('.gitmodules', '.gitattributes', '.gitignore', 'agents.md', 'security.md') or
            basename.startswith(('license', 'copying'))):
            raise GuardRejected('Protected file: ' + path)
        out.append(path)
    raw_modes = git_raw('diff', '--raw', '-z', old, new)
    if raw_modes and not raw_modes.endswith(b'\x00'):
        raise GuardRejected('Malformed raw diff')
    chunks = raw_modes[:-1].split(b'\x00') if raw_modes else []
    index = 0
    while index < len(chunks):
        fields = chunks[index].decode('ascii', 'strict').split()
        index += 1
        if len(fields) != 5:
            raise GuardRejected('Malformed raw diff header')
        source_mode, target_mode = fields[0][1:], fields[1]
        if source_mode not in ('000000', '100644', '100755') or target_mode not in ('100644', '100755'):
            raise GuardRejected('Unexpected file type or symlink in update')
        if index >= len(chunks):
            raise GuardRejected('Malformed raw diff path')
        index += 1
    return out

def changed_lines(old, new):
    raw = git_raw('diff', '--numstat', '-z', old, new)
    if raw and not raw.endswith(b'\x00'):
        raise GuardRejected('Malformed diff statistics')
    additions = deletions = 0
    for entry in raw.split(b'\x00'):
        if not entry:
            continue
        fields = entry.split(b'\t', 2)
        if len(fields) != 3:
            raise GuardRejected('Unexpected diff statistics')
        a, d, _ = fields
        if not a.isdigit() or not d.isdigit():
            raise GuardRejected('Binary file update')
        additions += int(a)
        deletions += int(d)
    return additions, deletions

def archive_latest_upstream(new):
    """Archive upstream HEAD without changing the original main or protected safe-sync."""
    tag = 'upstream-snapshots/' + new[:16]
    git('push', 'origin', new + ':refs/tags/' + tag)
    # This pointer is intentionally unreviewed and is the ONLY branch that may rewind.
    branch = 'refs/heads/upstream-latest-unreviewed'
    existing = git('ls-remote', '--heads', 'origin', branch).stdout.strip()
    if existing:
        previous = existing.split()[0]
        if previous != new:
            git('push', 'origin', '--force-with-lease=' + branch + ':' + previous, new + ':' + branch)
    else:
        git('push', 'origin', new + ':' + branch)
    return tag

def report(repo, upstream, new, tag, outcome, detail):
    print(outcome + ':', repo, 'upstream=' + new[:12], 'snapshot=' + tag, detail)
    if outcome == 'REVIEW_REQUIRED':
        warning = (repo + ': ' + detail).replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
        print('::warning title=Archived but awaiting review::' + warning)
    path = os.getenv('GITHUB_STEP_SUMMARY')
    if path:
        # The report is operational metadata; never execute code from the target.
        with open(path, 'a', encoding='utf-8') as handle:
            handle.write('### ' + repo + ': ' + outcome + '\n\n')
            handle.write('- Source: ' + upstream + '\n')
            handle.write('- Latest upstream commit: ' + new + '\n')
            handle.write('- Immutable archive tag: ' + tag + '\n')
            handle.write('- Latest raw branch: ' + 'upstream-latest-unreviewed' + '\n')
            handle.write('- Safe sync branch: ' + ('unchanged; review needed' if outcome == 'REVIEW_REQUIRED' else outcome) + '\n')
            handle.write('- Reason: ' + detail.replace('\n', ' ') + '\n\n')

def report_unavailable(repo, upstream, reason):
    message = repo + ': upstream ' + upstream + ' is currently unavailable (' + reason + '); existing snapshots and safe-sync remain unchanged'
    print('SOURCE_UNAVAILABLE:', message)
    print('::warning title=Upstream source unavailable::' + message)
    summary_path = os.getenv('GITHUB_STEP_SUMMARY')
    if summary_path:
        with open(summary_path, 'a', encoding='utf-8') as out:
            out.write('### ' + repo + ': SOURCE_UNAVAILABLE\n\n')
            out.write('- Upstream: ' + upstream + '\n')
            out.write('- Status: unavailable (possibly deleted, private or renamed); no new snapshot possible\n')
            out.write('- Existing main, archive, safe-sync and historical tags preserved\n\n')

def run():
    repo = os.environ['FORK_REPOSITORY']
    upstream = os.environ['UPSTREAM']
    upstream_id = int(os.environ['UPSTREAM_ID'])
    token = os.environ['GH_TOKEN']
    request = urllib.request.Request(
        'https://api.github.com/repos/' + upstream,
        headers={
            'Authorization': 'Bearer ' + token,
            'Accept': 'application/vnd.github+json',
            'User-Agent': 'ffint-guarded-fork-sync',
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            report_unavailable(repo, upstream, 'HTTP 404')
            return
        raise
    if (data['id'] != upstream_id or
        data['full_name'].casefold() != upstream.casefold() or
        data['default_branch'] != 'main' or data['archived']):
        raise Block('Upstream identity/status changed')
    if git('rev-parse', '--is-shallow-repository').stdout.strip() == 'true':
        git('fetch', '--no-tags', '--unshallow', 'origin', '+refs/heads/safe-sync:refs/remotes/origin/safe-sync')
    else:
        git('fetch', '--no-tags', 'origin', '+refs/heads/safe-sync:refs/remotes/origin/safe-sync')
    old = git('rev-parse', 'refs/remotes/origin/safe-sync').stdout.strip()
    original = os.environ['INITIAL_SHA']
    if not ancestor(original, old):
        raise Block('Archived base is no longer an ancestor of safe-sync')
    git('fetch', '--no-tags', 'https://github.com/' + upstream + '.git', 'refs/heads/main')
    new = git('rev-parse', 'FETCH_HEAD').stdout.strip()

    # The raw snapshot is deliberately independent of the heuristic review gate.
    # We NEVER execute upstream code and NEVER push to the fork's main.
    # An immutable tag is created before updating the explicitly unreviewed pointer.
    archive_tag = archive_latest_upstream(new)

    if old == new:
        report(repo, upstream, new, archive_tag, 'UNCHANGED', 'safe-sync already matches latest upstream')
        return

    try:
        if not ancestor(old, new):
            raise GuardRejected('Upstream history rewrite or divergence')
        commit_count = int(git('rev-list', '--count', old + '..' + new).stdout)
        if commit_count > 100:
            raise GuardRejected('Too many commits: ' + str(commit_count))
        files = diff_files(old, new)
        if len(files) > 120:
            raise GuardRejected('Too many changed files: ' + str(len(files)))
        added, deleted = changed_lines(old, new)
        if deleted > 5000 or deleted > added * 2 + 300:
            raise GuardRejected('Suspiciously large deletion: ' + str(deleted))
    except GuardRejected as exc:
        report(repo, upstream, new, archive_tag, 'REVIEW_REQUIRED', str(exc))
        return

    backup_tag = 'safe-sync-backup/' + old[:12]
    old_tag = git('rev-parse', 'refs/tags/' + backup_tag, ok=True)
    if old_tag.returncode == 0 and old_tag.stdout.strip() != old:
        raise Block('Conflicting backup tag')
    if old_tag.returncode:
        git('tag', backup_tag, old)
    git('push', 'origin', 'refs/tags/' + backup_tag)
    git('push', 'origin', new + ':refs/heads/safe-sync')
    report(repo, upstream, new, archive_tag, 'SAFE_UPDATED',
           'commits=' + str(commit_count) + ' files=' + str(len(files)) +
           ' additions=' + str(added) + ' deletions=' + str(deleted) +
           ' backup=' + backup_tag)

if __name__ == '__main__':
    try:
        run()
    except Exception as exc:
        error = str(exc)
        print('SYNC_ERROR:', error, file=sys.stderr)
        if 'refusing to allow a Personal Access Token to create or update workflow' in error:
            print('::error title=Workflows permission missing::Your GitHub fine-grained PAT needs repository Workflows: Read and write to preserve upstream commits containing changed .github/workflows files. Limit this permission to the affected forks.')
            print('ACTION_NEEDED: Edit the existing fine-grained PAT; add Workflows: Read and write for the affected repositories, then re-run the controller. Without it, new upstream commits cannot be archived as Git refs.')
        summary_path = os.getenv('GITHUB_STEP_SUMMARY')
        if summary_path:
            with open(summary_path, 'a', encoding='utf-8') as out:
                out.write('### SYNC_ERROR\n\n')
                out.write('- Error: ' + error.replace('\n', ' ').replace('|', '/')[:700] + '\n')
                out.write('- The original main and archived base are unchanged\n')
                out.write('- Missing Workflows permission on a token must not be treated as a successful backup\n\n')
        sys.exit(1)
