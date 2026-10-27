#!/usr/bin/env python3
"""Host-only real run smoke. Unit tests replace subprocess with fixtures."""
from __future__ import annotations
import argparse
import json
import os
import re
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def read_object(path):
    # Windows PowerShell writes UTF-8 with a BOM by default.
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('expected JSON object')
    return value


def run_smoke(output_dir, *, runner=None, timeout=1900):
    runner = runner or subprocess.run
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        return {'layers': {}, 'passed': False, 'error': 'output_directory_not_empty'}
    layers = {'decision': {'passed': False}, 'worker_started': {'passed': False},
              'turn_completed': {'passed': False}, 'independent_acceptance': {'passed': False}}
    report = {'layers': layers, 'passed': False}
    # Never bypass the production recursion guard, including inherited markers.
    if os.environ.get('LUNA_ROUTER_WORKER') == '1':
        report['error'] = 'host_execution_required_recursive_worker'
    else:
        try:
            with tempfile.TemporaryDirectory(prefix='luna-live-smoke-') as folder:
                base = Path(folder)
                project = base / 'project'
                project.mkdir()
                nonce = secrets.token_hex(24)
                numbers = [secrets.randbelow(1000000) for _ in range(7)]
                source = {'numbers': numbers, 'nonce': nonce}
                (output / 'input.json').write_text(json.dumps(source), encoding='utf-8')
                (project / 'input.json').write_text(json.dumps(source), encoding='utf-8')
                prompt = base / 'task.txt'
                prompt.write_text('Read input.json. Compute the integer sum of numbers. Write result.json '
                                  'as a JSON object with exactly two fields: answer (the computed integer sum) '
                                  'and nonce (copied exactly from input.json). Do not call any router.', encoding='utf-8')
                receipt = base / 'receipt.json'
                completed = runner([sys.executable, str(ROOT / 'luna.py'), 'run',
                                    '--prompt-file', str(prompt), '--project', str(project),
                                    '--data-policy', 'metadata_only', '--sandbox', 'workspace-write',
                                    '--state-dir', str(base / 'state'), '--output', str(receipt)],
                                   capture_output=True, text=True, encoding='utf-8', timeout=timeout)
                report['returncode'] = completed.returncode
                if receipt.exists():
                    shutil.copyfile(receipt, output / 'receipt.json')
                data = read_object(receipt)
                plan = data.get('plan', {})
                execution = data.get('execution', {})
                if not isinstance(plan, dict) or not isinstance(execution, dict):
                    raise ValueError('malformed_receipt')
                reason = plan.get('reason') or data.get('reason') or data.get('message')
                if isinstance(reason, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,100}', reason):
                    report['router_reason'] = reason
                if type(data.get('http_status')) is int:
                    report['http_status'] = data['http_status']
                layers['decision'] = {'passed': plan.get('status') == 'planned'
                                      and plan.get('simulation') is False
                                      and plan.get('decision_call_attempted') is True
                                      and plan.get('decision_response_received') is True
                                      and plan.get('decision_model_called') is True
                                      and plan.get('decision_source') == 'luna_decisions'
                                      and isinstance(plan.get('decision_response_id'), str)
                                      and bool(plan.get('decision_response_id').strip()),
                                      'response_id': plan.get('decision_response_id')}
                layers['worker_started']['passed'] = data.get('worker_started') is True and execution.get('worker_started') is True
                layers['turn_completed']['passed'] = (data.get('worker_completed') is True
                    and execution.get('status') == 'turn_completed' and execution.get('exit_code') == 0
                    and data.get('status') not in {'blocked', 'failed', 'error'})
                artifact = project / 'result.json'
                if artifact.exists():
                    shutil.copyfile(artifact, output / 'result.json')
                # Expected values live in this host process, outside worker project.
                actual = read_object(artifact)
                layers['independent_acceptance']['passed'] = (set(actual) == {'answer', 'nonce'}
                    and type(actual['answer']) is int and actual['answer'] == sum(numbers)
                    and actual['nonce'] == nonce)
                report['passed'] = completed.returncode == 0 and all(layer['passed'] for layer in layers.values())
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            # Do not echo subprocess output or exception strings containing credentials/task text.
            report['error'] = type(exc).__name__
    (output / 'summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, help='Evidence directory; use a new directory per run')
    args = parser.parse_args(argv)
    report = run_smoke(args.output_dir)
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
