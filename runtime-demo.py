"""Offline illustration of step-boundary dynamic policy; not live Codex switch."""
import json
from runtime_controller import RuntimeState, StepEvidence, simulate
state=RuntimeState('gpt-6.1-sol','medium',['low','medium','high','xhigh','max'],hard_floor='low')
events=[StepEvidence('routine',True),StepEvidence('routine',True),StepEvidence('complex',True),StepEvidence('verified_failure',True),StepEvidence('verified_failure',True),StepEvidence('routine',True),StepEvidence('routine',True),StepEvidence('verified_success',True)]
print(json.dumps(simulate(state,events), ensure_ascii=False,indent=2))
