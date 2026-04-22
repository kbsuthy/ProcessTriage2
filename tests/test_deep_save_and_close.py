import unittest
from unittest.mock import patch

from Web_app import app


class DeepSaveAndCloseTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_deep_save_and_close_persists_and_redirects(self):
        record = {
            'id': 'S123',
            'path': 'deep',
            'name': 'Invoice Intake',
            'purpose': 'Reduce manual work',
            'type': 'C',
            'steps': [],
            'discussion_mode': 'deep',
            'llm_chat': [],
            'process_map': {},
            'process_map_mermaid': '',
            'user': {'name': 'Test User', 'email': 'test@example.com'},
        }

        saved_payload = {}

        def fake_upsert_partial_record(user, path, rec, rec_id):
            saved_payload['user'] = user
            saved_payload['path'] = path
            saved_payload['record'] = rec
            saved_payload['rec_id'] = rec_id

        with self.client.session_transaction() as session_data:
            session_data['user'] = {'name': 'Test User', 'email': 'test@example.com'}

        with patch('Web_app.find_record', return_value=record), \
                patch('Web_app.upsert_partial_record', side_effect=fake_upsert_partial_record), \
                patch('Web_app.current_user', return_value=record['user']):
            response = self.client.post('/record/S123/discussion?mode=deep', data={'cancel_action': 'save_and_close'})

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/items'))
        self.assertEqual(saved_payload['path'], 'deep')
        self.assertEqual(saved_payload['rec_id'], 'S123')
        self.assertEqual(saved_payload['record']['discussion_mode'], 'deep')
        self.assertGreater(len(saved_payload['record']['llm_chat']), 0)
        self.assertIn('process_map', saved_payload['record'])


if __name__ == '__main__':
    unittest.main()