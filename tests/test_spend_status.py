"""Provider-hosted spending-cap status is informational, not a local gate."""
import unittest
from unittest.mock import patch
import openrouter_backend as orb


class SpendStatusTests(unittest.TestCase):
    def test_returns_only_spend_cap_fields(self):
        response = {'data': {'limit': 5, 'limit_remaining': 3.24, 'limit_reset':'monthly',
                             'name':'SENSITIVE', 'hash':'secret-id', 'label':'PRIVATE',
                             'creator_user_id':'uid', 'usage':10}}
        with patch.object(orb, 'request_json', return_value=response) as api:
            status = orb.get_spend_status()
            api.assert_called_once_with('GET', '/api/v1/key')
        self.assertTrue(status['api_key_limit_configured'])
        self.assertEqual(status['limit_usd'], '5')
        self.assertEqual(status['limit_remaining_usd'], '3.24')
        self.assertEqual(status['limit_reset'], 'monthly')
        self.assertNotIn('SENSITIVE', str(status))
        self.assertNotIn('PRIVATE', str(status))
        self.assertNotIn('secret-id', str(status))

    def test_unlimited_key_is_not_mislabeled_free(self):
        with patch.object(orb, 'request_json', return_value={'data':{'limit':None,'limit_remaining':None,'limit_reset':None}}):
            status = orb.get_spend_status()
        self.assertFalse(status['api_key_limit_configured'])
        self.assertIsNone(status['limit_usd'])
        self.assertFalse(status['local_fee_check'])

    def test_invalid_key_metadata_is_rejected_without_model_call(self):
        for fields in ({'limit': 'NaN'}, {'limit':True}, {'limit':None,'limit_reset':'daily'},
                       {'limit':20, 'limit_reset':'yearly'}, {'limit':10,'limit_remaining':float('nan')}):
            with self.subTest(fields=fields), patch.object(orb, 'request_json', return_value={'data':fields}) as call:
                with self.assertRaises(orb.APIError):
                    orb.get_spend_status()
                call.assert_called_once_with('GET', '/api/v1/key')


if __name__ == '__main__':
    unittest.main()
