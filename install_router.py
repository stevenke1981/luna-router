#!/usr/bin/env python3
"""Install the engine + bound skill + launchers; optionally register local MCP.
Never reads/writes credential files or changes the global model/provider.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import uuid

ROOT=Path(__file__).resolve().parent
BEGIN='# BEGIN LUNA ROUTER V2.7 MANAGED MCP'
END='# END LUNA ROUTER V2.7 MANAGED MCP'
SERVER='luna_router_v270'


def verify_source(source):
    manifest=source/'MANIFEST.sha256'
    count=0
    seen=set()
    for line in manifest.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        digest,sep,name=line.partition('  ')
        relative=Path(name)
        if not sep or not re.fullmatch('[0-9a-f]{64}',digest) or relative.is_absolute() or '..' in relative.parts or name in seen:
            raise ValueError('invalid_release_manifest')
        target=source/relative
        if not target.is_file() or target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
            raise ValueError('release_manifest_mismatch: '+name)
        seen.add(name)
        count+=1
    required={'router.py','luna.py','credentials.py','luna_mcp.py','config.json',
              'skills/gpt-luna-decision/SKILL.md'}
    if not required <= seen:
        raise ValueError('release_manifest_missing_runtime')
    return count


def mcp_config(current, destination):
    block=(f'{BEGIN}\n[mcp_servers.{SERVER}]\n'
           f'command = {json.dumps(sys.executable)}\n'
           f'args = [{json.dumps(str(destination/"luna_mcp.py"))}]\n'
           'env_vars = ["OPENROUTER_API_KEY"]\n'
           'startup_timeout_sec = 15\ntool_timeout_sec = 1900\n'
           f'{END}\n')
    pattern=re.compile(r'(?m)^'+re.escape(BEGIN)+r'\r?\n.*?^'+re.escape(END)+r'\r?\n?',re.S)
    if BEGIN in current or END in current:
        if current.count(BEGIN)!=1 or current.count(END)!=1 or not pattern.search(current):
            raise ValueError('invalid_managed_mcp_block')
        updated=pattern.sub(lambda _:block,current)
    elif re.search(r'(?m)^\s*\[mcp_servers\.(?:'+SERVER+r'|"'+SERVER+r'")\]',current):
        raise ValueError('unmanaged_mcp_table_conflict')
    else:
        updated=current.rstrip()+'\n\n'+block
    # Python 3.11+ is required for installation, so validate before any writes.
    import tomllib
    parsed=tomllib.loads(updated)
    if parsed['mcp_servers'][SERVER]['tool_timeout_sec']!=1900:
        raise ValueError('invalid_mcp_registration')
    return updated


def install(source, codex_home, register_mcp=False):
    source=Path(source).resolve()
    home=Path(codex_home).resolve()
    destination=home/'luna-router'
    if source==destination or source in destination.parents or destination in source.parents:
        raise ValueError('extract_release_outside_install_destination')
    verified=verify_source(source)
    config=home/'config.toml'
    original=config.read_text(encoding='utf-8-sig') if config.exists() else ''
    new_config=mcp_config(original,destination) if register_mcp else None
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:6]
    backup=home/'luna-router-backups'/stamp
    home.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix='.luna-stage-',dir=home))
    skill=home/'skills'/'gpt-luna-decision'
    binpath=home/'bin'
    had_destination=destination.exists()
    # Refuse to adopt an unrelated directory.
    if had_destination and not (destination/'router.py').is_file():
        shutil.rmtree(staging)
        raise ValueError('destination_is_not_an_existing_router_install')
    try:
        shutil.copytree(source,staging,dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__','.state','*.pyc',
                                                     '.git','.tmp','.venv','.env','.env.*'))
        verify_source(staging)
        backup.mkdir(parents=True)
        if skill.exists():
            shutil.copytree(skill,backup/'skill')
        if binpath.exists():
            for name in ('luna.ps1','luna.cmd'):
                if (binpath/name).exists():
                    (backup/'bin').mkdir(exist_ok=True)
                    shutil.copy2(binpath/name,backup/'bin'/name)
        if config.exists() and register_mcp:
            shutil.copy2(config,backup/'config.toml')
        if had_destination:
            destination.rename(backup/'runtime')
        staging.rename(destination)
        if had_destination and (backup/'runtime'/'.state').exists():
            shutil.copytree(backup/'runtime'/'.state',destination/'.state')
        skill.mkdir(parents=True,exist_ok=True)
        instructions=(destination/'skills/gpt-luna-decision/SKILL.md').read_text(encoding='utf-8')
        instructions=instructions.replace('{{ROUTER_PATH}}',str(destination/'luna.py'))
        instructions=instructions.replace('{{PYTHON_PATH}}',sys.executable)
        (skill/'SKILL.md').write_text(instructions,encoding='utf-8')
        binpath.mkdir(parents=True,exist_ok=True)
        ps='''param([Parameter(ValueFromRemainingArguments=$true)][string[]]$RouterArguments)
$ErrorActionPreference='Stop'
$Engine=Join-Path $PSScriptRoot '..\\luna-router\\luna.py'
if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 $Engine @RouterArguments }
else { & python $Engine @RouterArguments }
exit $LASTEXITCODE
'''
        (binpath/'luna.ps1').write_text(ps,encoding='utf-8-sig')
        (binpath/'luna.cmd').write_text('@echo off\nwhere py >nul 2>&1\nif errorlevel 1 (\n  python "%~dp0..\\luna-router\\luna.py" %*\n) else (\n  py -3 "%~dp0..\\luna-router\\luna.py" %*\n)\nexit /b %errorlevel%\n',encoding='utf-8')
        if register_mcp:
            temp_config=config.with_name('.luna-config-'+uuid.uuid4().hex+'.tmp')
            temp_config.write_text(new_config,encoding='utf-8')
            temp_config.replace(config)
        result={'status':'installed','version':'2.7.0','runtime':str(destination),
                'launcher':str(binpath/'luna.ps1'),'skill':str(skill/'SKILL.md'),
                'backup':str(backup),'manifest_verified_files':verified,
                'mcp_registered':register_mcp,'mcp_server':SERVER if register_mcp else None,
                'inference_performed':False,'global_model_changed':False,
                'restart_codex_required':True}
        (destination/'.state').mkdir(exist_ok=True)
        (destination/'.state'/'installation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return result
    except Exception:
        # Restore only files owned/touched by this installer; keep backup intact.
        if (backup/'runtime').exists():
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(backup/'runtime',destination)
        elif not had_destination and destination.exists():
            shutil.rmtree(destination)
        if (backup/'skill').exists():
            if skill.exists():
                shutil.rmtree(skill)
            shutil.copytree(backup/'skill',skill)
        if register_mcp and (backup/'config.toml').exists():
            shutil.copy2(backup/'config.toml',config)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex-home',type=Path,default=Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex'))
    parser.add_argument('--register-mcp',action='store_true',help='Append/update ONLY our managed MCP table; no model/provider change')
    args=parser.parse_args()
    try:
        print(json.dumps(install(ROOT,args.codex_home,args.register_mcp),ensure_ascii=False,indent=2))
        return 0
    except (OSError,ValueError,ImportError) as exc:
        print(json.dumps({'status':'install_failed','reason':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 2

if __name__=='__main__':
    raise SystemExit(main())
