import unittest
from unittest.mock import patch

from Web_app import app


class QuickResultDeepLinkTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_quick_result_deep_dive_links_to_discussion(self):
        record = {
            'id': 'S001',
            'path': 'quick',
            'name': 'Invoice Intake',
            'purpose': 'Reduce manual work',
            'type': 'C',
            'steps': [],
            'user': {'name': 'Test User', 'email': 'test@example.com'},
            'score': {'percent': 78, 'recommendation': 'Strong candidate for improvement'},
        }

        with self.client.session_transaction() as session_data:
            session_data['user'] = {'name': 'Test User', 'email': 'test@example.com'}

        with patch('Web_app.find_record', return_value=record), patch('Web_app.current_user', return_value=record['user']):
            response = self.client.get('/record/S001')

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('/record/S001/discussion?mode=deep', body)
        self.assertNotIn('/record/S001/edit', body)


if __name__ == '__main__':
    unittest.main()