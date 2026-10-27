#!/usr/bin/env python3
"""Installed executable entrypoint. No subcommand means RUN, not PLAN."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
import router
from credentials import resolve_key, key_status
from decision import _choice_answer, is_decision_model

COMMANDS = {'run','plan','doctor','discover','catalog','demo','quota','spend-status',
            'budget','fee-policy','retry-status','feedback'}

def probe(output=None):
    """Make exactly one real Decisions POST, independently of Codex discovery."""
    result = {'version':router.VERSION, 'operation':'probe', 'status':'blocked',
              'decision_model_called':False, 'decision_call_attempted':False,
              'worker_started':False, 'key_status':key_status()}
    try:
        if not resolve_key()[0]:
            raise ValueError('openrouter_key_missing_online_required')
        body = {'model':router.DECISION_MODEL,
                'state':{'purpose':'User-requested routing connection test', 'sample':'ready'},
                'questions':{'check':{'type':'choice', 'instructions':'Select the exact sample value.',
                                      'criteria':{'ready':'The sample is ready','other':'Any other sample'}}}}
        result['decision_call_attempted'] = True
        router.emit_event('luna_request_started', operation='probe', model=router.DECISION_MODEL)
        raw = router.orb.decide(body, None, {'model':router.DECISION_MODEL,'enabled':True})
        result['response_id'] = raw.get('id')
        result['response_model'] = raw.get('model')
        if not is_decision_model(raw.get('model')):
            raise ValueError('decision_model_mismatch')
        _choice_answer(raw.get('answers',{}).get('check'), ['ready','other'], 'check')
        result.update(status='response_received', decision_model_called=True,
                      decision_source='luna_decisions', answers=raw['answers'],
                      reported_cost_usd=router.orb.reported_cost(raw))
    except (router.orb.APIError, ValueError) as exc:
        result['error_type'] = type(exc).__name__
        result['reason'] = str(exc)
        if isinstance(exc,router.orb.APIError):
            result['http_status'] = exc.status
            result['retry_after'] = exc.retry_after
    path = Path(output) if output else router.ROOT/'.state'/'probe-result.json'
    result['receipt_path'] = str(path.resolve())
    router.save_json(path, result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['status']=='response_received' else 2


def main(argv=None):
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure:
            options = {'encoding': 'utf-8'}
            if stream is not sys.stdin:
                options['errors'] = 'backslashreplace'
            reconfigure(**options)
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0]=='probe':
        parser=argparse.ArgumentParser(description='One live Luna API request. No Codex worker. Uses OpenRouter key spend settings.')
        parser.add_argument('--output')
        parsed=parser.parse_args(args[1:])
        return probe(parsed.output)
    if not args or args[0] not in COMMANDS:
        args.insert(0,'run')
    return router.main(args)

if __name__=='__main__':
    raise SystemExit(main())
