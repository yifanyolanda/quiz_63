import io
import json
import tempfile
import unittest
from unittest.mock import patch
from app import create_app


class QuizTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(self.tmp.name+'/test.db')
        self.client = self.app.test_client()
        self.clock = patch('app.time.time', return_value=1000)
        self.now = self.clock.start()
        self.client.get('/')
        with self.client.session_transaction() as s:
            self.csrf = s['csrf']
        self.questions = [dict(question=f'题{i}', options=['一','二','三','四'], answer='A') for i in range(100)]
        with self.app.app_context():
            import sqlite3
            with sqlite3.connect(self.app.config['DATABASE']) as conn:
                conn.execute('INSERT INTO banks(questions) VALUES (?)', (json.dumps(self.questions),))

    def tearDown(self):
        self.clock.stop()
        self.tmp.cleanup()

    def act(self, action=None, **data):
        if action:
            r = self.client.post('/api/state', json=dict(action=action, **data), headers={'X-CSRF-Token':self.csrf})
        else:
            r = self.client.get('/api/state')
        self.assertEqual(r.status_code, 200)
        return r.get_json()

    def start(self):
        self.act()
        self.act('id', id='微信群昵称 <test>')
        return self.act('start')

    def test_two_exams_and_score(self):
        s = self.start()
        seen = set()
        for attempt in range(2):
            for n in range(1,4):
                self.assertNotIn(s['question'], seen)
                seen.add(s['question'])
                self.assertNotIn('answer', s)
                s = self.act('answer', number=n, answer='A')
            self.assertEqual(s['result']['score'], 3)
            if attempt == 0:
                self.act('retry')
                self.act('id', id='新昵称')
                s = self.act('start')
        self.assertEqual(self.act('retry')['stage'], 'result')
        self.assertEqual(self.act('start')['attempts'], 2)

    def test_id_two_timeouts(self):
        self.act()
        self.now.return_value = 1061
        self.assertIsNone(self.act()['deadline'])
        self.act('retry')
        self.now.return_value = 1122
        self.assertEqual(self.act('id', id='迟到')['stage'], 'blocked')
        self.assertEqual(self.act('retry')['stage'], 'blocked')

    def test_offline_timeout_and_late_answer(self):
        self.start()
        self.now.return_value = 1011
        s = self.act('answer', number=1, answer='A')
        self.assertEqual(s['number'], 2)
        self.assertEqual(s['deadline'], 1020)
        self.now.return_value = 1031
        s = self.act()
        self.assertEqual(s['result']['score'], 0)

    def test_csrf_and_upload_validation(self):
        self.assertEqual(self.client.post('/api/state', json={'action':'retry'}).status_code, 403)
        self.assertEqual(self.client.get('/admin/template.csv').status_code, 302)
        with self.client.session_transaction() as s:
            s['admin'] = True
        def upload(content):
            return self.client.post('/admin/upload', data={'csrf':self.csrf, 'file':(io.BytesIO(content.encode()), 'q.csv')})
        self.assertEqual(upload('question,A,B,C,D,answer\n题,a,b,c,d,A\n').status_code, 400)
        valid = 'question,A,B,C,D,answer\n'+''.join(f'新题{i},a,b,c,d,B\n' for i in range(100))
        self.assertEqual(upload(valid).status_code, 302)
        self.assertTrue(self.start()['question'].startswith('新题'))

    def test_version_retained(self):
        self.start()
        import sqlite3
        with sqlite3.connect(self.app.config['DATABASE']) as conn:
            conn.execute('INSERT INTO banks(questions) VALUES (?)', (json.dumps([dict(q, question='替换题') for q in self.questions]),))
        self.now.return_value=1031
        self.act()
        self.act('retry')
        self.act('id', id='同一浏览器')
        self.assertNotEqual(self.act('start')['question'], '替换题')


if __name__ == '__main__':
    unittest.main()
