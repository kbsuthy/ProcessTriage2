import unittest
from unittest.mock import patch

from Web_app import app


class DeepFormRegressionTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_deep_validation_keeps_numbered_answers(self):
        captured = {}

        def fake_render_template(template_name, **context):
            captured['template_name'] = template_name
            captured['context'] = context
            return f"{context.get('error', '')}\n{context.get('values', {})}"

        payload = {
            'process_name': '',
            'purpose': 'Reduce manual effort',
            'description': 'A high-level process description',
            'step_count': '4',
            'process_type': 'C',
            'frequency': 'frequently',
            'involvement': ['one_person', 'small_group'],
            'frustration': 'frequent',
            'impact': 'rework',
            'consistency': 'often_varies',
            'tools': 'many',
            'flagged': 'multiple',
            'improvement_benefit': ['time', 'errors'],
        }

        with self.client.session_transaction() as session_data:
            session_data['user'] = {'name': 'Test User', 'email': 'test@example.com'}

        with patch('Web_app.render_template', side_effect=fake_render_template), patch('Web_app.upsert_partial_record', return_value=None):
            response = self.client.post('/deep', data=payload)

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured['template_name'], 'deep.html')
        self.assertIn('Please provide process name.', captured['context']['error'])
        self.assertNotIn('first step', captured['context']['error'].lower())
        self.assertEqual(captured['context']['values']['frequency'], 'frequently')
        self.assertEqual(captured['context']['values']['frustration'], 'frequent')
        self.assertEqual(captured['context']['values']['improvement_benefit'], ['time', 'errors'])
        self.assertIn('frequently', body)
        self.assertIn('frequent', body)


if __name__ == '__main__':
    unittest.main()