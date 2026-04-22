import unittest
from unittest.mock import patch

from Web_app import (
    app,
    build_mermaid_flow,
    deep_dive_intro_message,
    enforce_backbone_on_map,
    extract_first_map_step_label_from_mermaid,
    normalize_legacy_deep_intro,
    resolve_first_step_label_for_intro,
)


class DeepBackboneMapTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_deep_discussion_uses_description_backbone(self):
        record = {
            'id': 'S777',
            'path': 'deep',
            'name': 'Invoice Processing',
            'purpose': 'Improve cycle time',
            'type': 'C',
            'description': '1. Receive request from email\n2. Validate required fields\n3. Approve and notify requester',
            'steps': [],
            'llm_chat': [],
            'user': {'name': 'Test User', 'email': 'test@example.com'},
        }

        with self.client.session_transaction() as session_data:
            session_data['user'] = {'name': 'Test User', 'email': 'test@example.com'}

        with patch('Web_app.find_record', return_value=record), patch('Web_app.current_user', return_value=record['user']):
            response = self.client.get('/record/S777/discussion?mode=deep&choice=continue')

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Triage Assistant:', body)
        self.assertIn("Let&#39;s start with Step 1:", body)
        self.assertIn('who is involved', body)
        self.assertIn('Receive request from email', body)
        self.assertIn('Validate required fields', body)
        self.assertIn('Approve and notify requester', body)

    def test_backbone_enforcement_places_microsteps_below(self):
        backbone = ['Receive request', 'Validate request', 'Approve request']
        candidate = {
            'summary': ['Candidate summary'],
            'steps': [
                {'id': 'S1', 'text': 'Receive request', 'lane': 'main', 'team': 'Ops', 'parallel_of': None},
                {'id': 'X1', 'text': 'Capture ticket metadata', 'lane': 'below', 'team': 'Ops', 'parallel_of': 'S1'},
                {'id': 'S4', 'text': 'Unexpected extra main step', 'lane': 'main', 'team': 'Ops', 'parallel_of': None},
                {'id': 'X2', 'text': 'Route to approver', 'lane': 'above', 'team': 'Ops', 'parallel_of': 'S3'},
            ],
        }

        mapped = enforce_backbone_on_map(candidate, backbone, [])
        steps = mapped.get('steps', [])

        main_steps = [s for s in steps if s.get('lane') == 'main']
        self.assertEqual([s.get('text') for s in main_steps], backbone)

        child_steps = [s for s in steps if s.get('lane') == 'below']
        self.assertGreaterEqual(len(child_steps), 2)
        self.assertTrue(any(s.get('parallel_of') == 'S1' for s in child_steps))
        self.assertTrue(any(s.get('parallel_of') == 'S3' for s in child_steps))

    def test_existing_deep_chat_legacy_intro_is_updated(self):
        record = {
            'id': 'S778',
            'path': 'deep',
            'name': 'Invoice Processing',
            'purpose': 'Improve cycle time',
            'type': 'C',
            'description': 'Receive request\nValidate request\nApprove request',
            'steps': [],
            'llm_chat': [
                {
                    'role': 'assistant',
                    'content': (
                        'Before we begin, here is a quick recap: this is "Invoice Processing". '
                        'I will ask a few focused questions to better understand the workflow, pain points, and constraints so the guidance is more useful. '
                        'Are you okay to proceed?'
                    ),
                    'timestamp': '2026-04-18T00:00:00+00:00',
                }
            ],
            'user': {'name': 'Test User', 'email': 'test@example.com'},
        }

        save_calls = {'count': 0}

        def fake_upsert(*args, **kwargs):
            save_calls['count'] += 1

        with self.client.session_transaction() as session_data:
            session_data['user'] = {'name': 'Test User', 'email': 'test@example.com'}

        with patch('Web_app.find_record', return_value=record), \
                patch('Web_app.current_user', return_value=record['user']), \
                patch('Web_app.upsert_partial_record', side_effect=fake_upsert):
            response = self.client.get('/record/S778/discussion?mode=deep&choice=continue')

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Let&#39;s start with Step 1:", body)
        self.assertNotIn('Before we begin, here is a quick recap', body)
        self.assertEqual(save_calls['count'], 1)

    def test_intro_uses_same_label_as_first_map_tile(self):
        record = {
            'id': 'S779',
            'path': 'deep',
            'name': 'AI Agent Request for Division',
            'description': (
                'An internal division identifies a need for an AI application based on repeated manual work and submits a request for review.\n'
                'Intake team validates request completeness.\n'
                'Governance team approves prioritization.'
            ),
            'steps': [],
        }

        process_map = {
            'summary': ['demo'],
            'steps': [
                {'id': 'S1', 'text': 'An internal division identifies a need for an AI application based on repeated manual work and submits a request for review.', 'lane': 'main', 'team': 'Current Team', 'parallel_of': None},
                {'id': 'S2', 'text': 'Intake team validates request completeness.', 'lane': 'main', 'team': 'Current Team', 'parallel_of': None},
            ],
        }
        mermaid = build_mermaid_flow(process_map)
        first_tile_label = extract_first_map_step_label_from_mermaid(mermaid)

        resolved_label = resolve_first_step_label_for_intro(record, process_map=process_map, process_map_mermaid=mermaid)
        intro = deep_dive_intro_message(record, first_step_label=resolved_label)

        self.assertTrue(first_tile_label)
        self.assertEqual(resolved_label, first_tile_label)
        self.assertIn(f'"{first_tile_label}"', intro)

    def test_legacy_intro_normalization_uses_stored_mermaid_first_tile(self):
        record = {
            'id': 'S780',
            'path': 'deep',
            'name': 'AI Agent Request for Division',
            'description': 'An internal division identifies a need for an AI application and submits a request.',
            'steps': [],
            'process_map': {
                'summary': ['demo'],
                'steps': [
                    {'id': 'S1', 'text': 'An internal division identifies a need for an AI application and submits a request.', 'lane': 'main', 'team': 'Current Team', 'parallel_of': None}
                ],
            },
            'process_map_mermaid': 'flowchart LR\nN1["Identify AI application need."]\n',
        }
        messages = [
            {
                'role': 'assistant',
                'content': 'Let us start with step 1 for "AI Agent Request for Division": "An internal division identifies a need for an AI a...". Please describe it in a little more detail: who is involved, how long it usually takes, what triggers it, which tools are used, and what output it creates.',
                'timestamp': '2026-04-20T00:00:00+00:00',
            }
        ]

        updated, changed = normalize_legacy_deep_intro(
            record,
            messages,
            process_map=record['process_map'],
            process_map_mermaid=record['process_map_mermaid'],
        )

        self.assertTrue(changed)
        self.assertIn('"Identify AI application need."', updated[0]['content'])

    def test_deep_dive_post_redirects_back_to_continue_mode(self):
        record = {
            'id': 'S781',
            'path': 'deep',
            'name': 'Invoice Processing',
            'description': 'Receive request\nValidate request\nApprove request',
            'steps': [],
            'llm_chat': [
                {'role': 'assistant', 'content': 'Let\'s start with Step 1: "Receive request". Please describe it in a little more detail: who is involved, how long it usually takes, what triggers it, which tools are used, and what output it creates.', 'timestamp': '2026-04-20T00:00:00+00:00'},
                {'role': 'user', 'content': 'The request arrives by email.', 'timestamp': '2026-04-20T00:01:00+00:00'},
                {'role': 'assistant', 'content': 'Thanks, that helps map step 1. What is step 2, including who performs it, how it starts, and what output it produces?', 'timestamp': '2026-04-20T00:02:00+00:00'},
            ],
            'process_map': {
                'summary': ['demo'],
                'steps': [
                    {'id': 'S1', 'text': 'Receive request', 'lane': 'main', 'team': 'Current Team', 'parallel_of': None},
                    {'id': 'S2', 'text': 'Validate request', 'lane': 'main', 'team': 'Current Team', 'parallel_of': None},
                ],
            },
            'process_map_mermaid': 'flowchart LR\nN1["Receive request"]\nN2["Validate request"]\n',
            'discussion_mode': 'deep',
            'deep_dive_complete': False,
            'user': {'name': 'Test User', 'email': 'test@example.com'},
        }

        with self.client.session_transaction() as session_data:
            session_data['user'] = {'name': 'Test User', 'email': 'test@example.com'}

        saved = {'count': 0}

        def fake_upsert(*args, **kwargs):
            saved['count'] += 1

        with patch('Web_app.find_record', return_value=record), \
                patch('Web_app.current_user', return_value=record['user']), \
                patch('Web_app.upsert_partial_record', side_effect=fake_upsert), \
                patch('Web_app.llm_reply', return_value='Thanks, that helps map step 1. What is step 2, including who performs it, how it starts, and what output it produces?'):
            response = self.client.post(
                '/record/S781/discussion?mode=deep',
                data={'message': 'The request arrives by email.'},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn('choice=continue', response.headers.get('Location', ''))
        self.assertEqual(saved['count'], 1)

    def test_legacy_deep_discussion_refreshes_process_map_from_saved_chat(self):
        record = {
            'id': 'S782',
            'path': 'deep',
            'name': 'Invoice Processing',
            'description': 'Receive request\nValidate request\nApprove request',
            'steps': [],
            'llm_chat': [
                {'role': 'assistant', 'content': 'Let\'s start with Step 1: "Receive request". Please describe it in a little more detail: who is involved, how long it usually takes, what triggers it, which tools are used, and what output it creates.', 'timestamp': '2026-04-20T00:00:00+00:00'},
                {'role': 'user', 'content': 'The request arrives by email.', 'timestamp': '2026-04-20T00:01:00+00:00'},
                {'role': 'assistant', 'content': 'Thanks, that helps map step 1. What is step 2, including who performs it, how it starts, and what output it produces?', 'timestamp': '2026-04-20T00:02:00+00:00'},
            ],
            'discussion_mode': 'deep',
            'deep_dive_complete': False,
            'process_map': {
                'summary': ['old map'],
                'steps': [
                    {'id': 'S1', 'text': 'Receive request', 'lane': 'main', 'team': 'Current Team', 'parallel_of': None},
                    {'id': 'S2', 'text': 'Validate request', 'lane': 'main', 'team': 'Current Team', 'parallel_of': None},
                ],
            },
            'process_map_mermaid': 'flowchart LR\nN1["Receive request"]\nN2["Validate request"]\n',
            'user': {'name': 'Test User', 'email': 'test@example.com'},
        }

        refreshed_map = {
            'summary': ['refreshed from chat'],
            'steps': [
                {'id': 'S1', 'text': 'Receive request', 'lane': 'main', 'team': 'Current Team', 'parallel_of': None},
                {'id': 'X1', 'text': 'Capture request details', 'lane': 'below', 'team': 'Current Team', 'parallel_of': 'S1'},
                {'id': 'S2', 'text': 'Validate request', 'lane': 'main', 'team': 'Current Team', 'parallel_of': None},
            ],
        }

        with self.client.session_transaction() as session_data:
            session_data['user'] = {'name': 'Test User', 'email': 'test@example.com'}

        save_calls = {'count': 0}

        def fake_upsert(*args, **kwargs):
            save_calls['count'] += 1

        with patch('Web_app.find_record', return_value=record), \
                patch('Web_app.current_user', return_value=record['user']), \
                patch('Web_app.extract_process_map_with_llm', return_value=refreshed_map), \
                patch('Web_app.upsert_partial_record', side_effect=fake_upsert):
            response = self.client.get('/record/S782/discussion?mode=deep&choice=continue')

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Capture request details', body)
        self.assertIn('refreshed from chat', body)
        self.assertEqual(save_calls['count'], 1)


if __name__ == '__main__':
    unittest.main()