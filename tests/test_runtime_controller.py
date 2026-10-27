import unittest
from runtime_controller import RuntimeState, StepEvidence, transition

class RuntimeControllerTests(unittest.TestCase):
    def setUp(self):
        self.base = RuntimeState('gpt-6.1-sol','medium',['low','medium','high','xhigh','max'])
    def test_complex_goes_up_before_failure(self):
        new, action = transition(self.base, StepEvidence('complex', True))
        self.assertEqual((new.effort, action), ('high','proactive_effort_up:medium->high'))
    def test_unknown_does_not_ramp(self):
        new, action = transition(self.base, StepEvidence('complex', False))
        self.assertEqual(new.effort, 'medium')
    def test_routine_two_down(self):
        new, _ = transition(self.base, StepEvidence('routine', True))
        new, action = transition(new, StepEvidence('routine', True))
        self.assertEqual((new.effort, action), ('low','cooldown_effort_down:medium->low'))
    def test_two_failures_up(self):
        new, _ = transition(self.base, StepEvidence('verified_failure', True))
        new, action = transition(new, StepEvidence('verified_failure', True))
        self.assertEqual((new.effort, action), ('high','effort_up:medium->high'))
    def test_ceiling_model_escalation_signal(self):
        state = RuntimeState('gpt-6.1-sol','max',['high','max'])
        state, _ = transition(state, StepEvidence('verified_failure', True))
        state, action = transition(state, StepEvidence('verified_failure', True))
        self.assertEqual(action, 'escalate_model_required_at_native_ceiling')
        self.assertEqual(state.model, 'gpt-6.1-sol')
    def test_blocker_does_not_escalate(self):
        new, _ = transition(self.base, StepEvidence('blocked',True))
        self.assertEqual(new.effort,'medium')
    def test_floor_prevents_overcooling(self):
        self.base.hard_floor='medium'
        s,_ = transition(self.base, StepEvidence('routine',True))
        s,_ = transition(s, StepEvidence('routine',True))
        self.assertEqual(s.effort,'medium')
    def test_success_requires_evidence(self):
        s,_ = transition(self.base, StepEvidence('verified_success',False))
        self.assertFalse(s.completed)
        s,_ = transition(s, StepEvidence('verified_success',True))
        self.assertTrue(s.completed)
    def test_unsupported_max_not_generated(self):
        state=RuntimeState('gpt-6-luna','high',['low','medium','high'])
        state, action=transition(state,StepEvidence('complex',True))
        self.assertEqual(action,'complex_at_ceiling')
    def test_state_nonmutating(self):
        new,_ = transition(self.base, StepEvidence('complex',True))
        self.assertEqual(self.base.effort,'medium')
        self.assertNotEqual(new.effort,self.base.effort)
if __name__ == '__main__': unittest.main()
