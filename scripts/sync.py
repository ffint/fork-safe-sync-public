#!/usr/bin/env python3
"""Fail-closed fast-forward of a fork's safe-sync branch; never execute upstream code."""
import json
import os
import subprocess
import sys
import urllib.request

class Block(Exception):
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
            raise Block('Malformed file-change list')
        return []
    parts = raw[:-1].split(b'\x00')
    out = []
    idx = 0
    while idx < len(parts):
        status = parts[idx].decode('ascii', 'strict')
        idx += 1
        if status not in ('A', 'M'):
            raise Block('File deletion/rename/type-change or unsupported status: ' + status)
        if idx >= len(parts):
            raise Block('Malformed file-change record')
        path = parts[idx].decode('utf-8', 'surrogateescape')
        idx += 1
        if not path:
            raise Block('Empty file path')
        name = path.casefold()
        basename = name.rsplit('/', 1)[-1]
        if (name.startswith('.github/') or
            name in ('.gitmodules', '.gitattributes', '.gitignore', 'agents.md', 'security.md') or
            basename.startswith(('license', 'copying'))):
            raise Block('Protected file: ' + path)
        out.append(path)
    raw_modes = git_raw('diff', '--raw', '-z', old, new)
    if raw_modes and not raw_modes.endswith(b'\x00'):
        raise Block('Malformed raw diff')
    chunks = raw_modes[:-1].split(b'\x00') if raw_modes else []
    index = 0
    while index < len(chunks):
        fields = chunks[index].decode('ascii', 'strict').split()
        index += 1
        if len(fields) != 5:
            raise Block('Malformed raw diff header')
        source_mode, target_mode = fields[0][1:], fields[1]
        if source_mode not in ('000000', '100644', '100755') or target_mode not in ('100644', '100755'):
            raise Block('Unexpected file type or symlink in update')
        if index >= len(chunks):
            raise Block('Malformed raw diff path')
        index += 1
    return out

def changed_lines(old, new):
    raw = git_raw('diff', '--numstat', '-z', old, new)
    if raw and not raw.endswith(b'\x00'):
        raise Block('Malformed diff statistics')
    additions = deletions = 0
    for entry in raw.split(b'\x00'):
        if not entry:
            continue
        fields = entry.split(b'\t', 2)
        if len(fields) != 3:
            raise Block('Unexpected diff statistics')
        a, d, _ = fields
        if not a.isdigit() or not d.isdigit():
            raise Block('Binary file update')
        additions += int(a)
        deletions += int(d)
    return additions, deletions

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
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.load(response)
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
    if old == new:
        print('NO CHANGE:', repo)
        return
    if not ancestor(old, new):
        raise Block('Upstream history rewrite or divergence')
    commit_count = int(git('rev-list', '--count', old + '..' + new).stdout)
    if commit_count > 100:
        raise Block('Too many commits: ' + str(commit_count))
    files = diff_files(old, new)
    if len(files) > 120:
        raise Block('Too many changed files: ' + str(len(files)))
    added, deleted = changed_lines(old, new)
    if deleted > 5000 or deleted > added * 2 + 300:
        raise Block('Suspiciously large deletion: ' + str(deleted))
    backup_tag = 'safe-sync-backup/' + old[:12]
    old_tag = git('rev-parse', 'refs/tags/' + backup_tag, ok=True)
    if old_tag.returncode == 0 and old_tag.stdout.strip() != old:
        raise Block('Conflicting backup tag')
    if old_tag.returncode:
        git('tag', backup_tag, old)
    git('push', 'origin', 'refs/tags/' + backup_tag)
    git('push', 'origin', new + ':refs/heads/safe-sync')
    print('SYNC OK:', repo, 'commits=', commit_count, 'files=', len(files),
          'additions=', added, 'deletions=', deleted, 'backup=', backup_tag)

if __name__ == '__main__':
    try:
        run()
    except Exception as exc:
        print('BLOCKED:', str(exc), file=sys.stderr)
        sys.exit(1)
