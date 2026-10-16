#!/usr/bin/env python3
"""Offline Git fixtures verify update success and destructive-update rejection."""
import json
import io
import os
import tempfile
import importlib.util
import subprocess
from pathlib import Path

script = Path(__file__).resolve().parents[1] / 'scripts' / 'sync.py'
spec = importlib.util.spec_from_file_location('safe_sync', script)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)

def git(*args, cwd, check=True):
    result = subprocess.run(['git', *args], cwd=cwd, text=True, capture_output=True)
    if result.returncode and check:
        raise AssertionError(str(args) + '\n' + result.stderr)
    return result.stdout.strip()

class API:
    def __enter__(self):
        return io.BytesIO(json.dumps({'id':123,'full_name':'demo/source','default_branch':'main','archived':False}).encode())
    def __exit__(self, *args):
        pass

with tempfile.TemporaryDirectory() as d:
    folder = Path(d)
    upstream, bare, work = folder/'upstream', folder/'fork.git', folder/'work'
    upstream.mkdir()
    git('init','-b','main',cwd=upstream)
    git('config','user.name','Tester',cwd=upstream)
    git('config','user.email','test@example.com',cwd=upstream)
    (upstream/'src').mkdir()
    (upstream/'src'/'a.py').write_text('print(1)\n')
    (upstream/'README.md').write_text('hello\n')
    git('add','.',cwd=upstream)
    git('commit','-m','initial',cwd=upstream)
    initial=git('rev-parse','HEAD',cwd=upstream)
    git('init','--bare',str(bare),cwd=folder)
    git('clone',str(upstream),str(work),cwd=folder)
    git('remote','set-url','origin',str(bare),cwd=work)
    git('push','origin',f'{initial}:refs/heads/main',cwd=work)
    git('push','origin',f'{initial}:refs/heads/safe-sync',cwd=work)
    (upstream/'src'/'a.py').write_text('print(2)\n')
    (upstream/'src'/'new.py').write_text('print(3)\n')
    git('add','.',cwd=upstream)
    git('commit','-m','normal update',cwd=upstream)
    good=git('rev-parse','HEAD',cwd=upstream)
    os.environ.update(FORK_REPOSITORY='ffint/sample', UPSTREAM='demo/source',
                      UPSTREAM_ID='123', INITIAL_SHA=initial, GH_TOKEN='test')
    os.chdir(work)
    sync.urllib.request.urlopen=lambda *args,**kwargs: API()
    original_git=sync.git
    def local_git(*args,**kwargs):
        a=list(args)
        if 'https://github.com/demo/source.git' in a:
            a[a.index('https://github.com/demo/source.git')]=str(upstream)
        return original_git(*a,**kwargs)
    sync.git=local_git
    sync.run()
    assert git('rev-parse','refs/heads/safe-sync',cwd=bare)==good
    assert git('rev-parse','refs/heads/main',cwd=bare)==initial
    sync.run()
    (upstream/'src'/'new.py').unlink()
    git('add','.',cwd=upstream)
    git('commit','-m','delete a file',cwd=upstream)
    try:
        sync.run()
        raise AssertionError('Deletion should have been blocked')
    except sync.Block as ex:
        assert 'deletion' in str(ex).lower()
    assert git('rev-parse','refs/heads/safe-sync',cwd=bare)==good
    git('reset','--hard',good,cwd=upstream)
    (upstream/'LICENSE').write_text('Changed license\n')
    git('add','.',cwd=upstream)
    git('commit','-m','change license',cwd=upstream)
    try:
        sync.run()
        raise AssertionError('Protected file change should have been blocked')
    except sync.Block as ex:
        assert 'protected' in str(ex).lower()
    git('reset','--hard',initial,cwd=upstream)
    (upstream/'src'/'a.py').write_text('rewritten\n')
    git('add','.',cwd=upstream)
    git('commit','-m','rewrite history',cwd=upstream)
    try:
        sync.run()
        raise AssertionError('History rewrite should have been blocked')
    except sync.Block as ex:
        assert 'history' in str(ex).lower()
    assert git('rev-parse','refs/heads/main',cwd=bare)==initial
    assert git('rev-parse','refs/heads/safe-sync',cwd=bare)==good

with tempfile.TemporaryDirectory() as d:
    wd=Path(d)
    git('init','-b','main',cwd=wd)
    git('config','user.name','Tester',cwd=wd)
    git('config','user.email','test@example.com',cwd=wd)
    (wd/'normal').write_text('ok\n')
    git('add','.',cwd=wd)
    git('commit','-m','base',cwd=wd)
    base=git('rev-parse','HEAD',cwd=wd)
    (wd/'linked').symlink_to('normal')
    git('add','.',cwd=wd)
    git('commit','-m','symlink',cwd=wd)
    os.chdir(wd)
    try:
        sync.diff_files(base,git('rev-parse','HEAD',cwd=wd))
        raise AssertionError('Symlink should have been blocked')
    except sync.Block as ex:
        assert 'symlink' in str(ex).lower()
    git('reset','--hard',base,cwd=wd)
    (wd/'odd\tfilename').write_text('hello\n')
    git('add','.',cwd=wd)
    git('commit','-m','tab in filename',cwd=wd)
    assert len(sync.diff_files(base,git('rev-parse','HEAD',cwd=wd)))==1
print('PASS: normal sync, no-change, deletion, protected file, history rewrite, symlink, filename parsing, main intact')
