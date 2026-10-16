import csv
import io
import json
import os
import secrets
import sqlite3
import time
from functools import wraps
from pathlib import Path
from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


def create_app(database=None):
    app = Flask(__name__, instance_path=os.environ.get('DATA_DIR') or str(Path(__file__).resolve().parent / 'instance'))
    root = Path(app.instance_path)
    root.mkdir(exist_ok=True)
    keyfile = root / 'secret'
    if not keyfile.exists():
        keyfile.write_text(secrets.token_hex(32))
        keyfile.chmod(0o600)
    app.secret_key = os.environ.get('SECRET_KEY') or keyfile.read_text()
    app.config.update(DATABASE=database or str(root / 'quiz.db'), MAX_CONTENT_LENGTH=1024*1024,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                      SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE') == '1')

    def db():
        conn = sqlite3.connect(app.config['DATABASE'], timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    with db() as conn:
        conn.executescript('''CREATE TABLE IF NOT EXISTS banks(id INTEGER PRIMARY KEY, questions TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);''')
        password = os.environ.get('ADMIN_PASSWORD')
        if password:
            conn.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('password', generate_password_hash(password)))

    @app.before_request
    def protect():
        session.setdefault('csrf', secrets.token_hex(24))
        if request.method == 'POST':
            token = request.headers.get('X-CSRF-Token') or request.form.get('csrf')
            if not token or not secrets.compare_digest(token, session['csrf']):
                abort(403)

    def admin_required(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not session.get('admin'):
                return redirect(url_for('admin'))
            return fn(*args, **kwargs)
        return wrapped

    @app.get('/')
    def index():
        return render_template('index.html')

    def advance(user, now):
        exam = user.get('active')
        if not exam:
            return
        while exam['index'] < 3 and now >= exam['deadline']:
            exam['index'] += 1
            exam['deadline'] += 10
        if exam['index'] == 3:
            user['results'].append({'id': exam['id'], 'score': exam['score'], 'time': int(now)})
            user['active'] = None

    def public(user, now):
        if user.get('active'):
            e = user['active']
            q = user['questions'][e['indices'][e['index']]]
            return dict(stage='exam', number=e['index']+1, question=q['question'],
                        options=q['options'], deadline=e['deadline'], now=now, attempt=user['attempts'])
        if user['timeouts'] >= 2:
            return dict(stage='blocked')
        if user['results'] and not user.get('form') and not user.get('ready'):
            return dict(stage='result', result=user['results'][-1], attempts=user['attempts'])
        if user.get('ready'):
            return dict(stage='ready', id=user['ready'], attempts=user['attempts'])
        return dict(stage='id', deadline=user['form'], now=now, timeouts=user['timeouts'])

    @app.route('/api/state', methods=['GET', 'POST'])
    def state():
        now = time.time()
        uid = session.setdefault('browser', secrets.token_hex(24))
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT data FROM users WHERE id=?', (uid,)).fetchone()
            user = json.loads(row['data']) if row else dict(timeouts=0, attempts=0, results=[], form=now+60)
            advance(user, now)
            if user.get('form') and now >= user['form']:
                user['timeouts'] += 1
                user['form'] = None
            payload = request.get_json(silent=True) or {}
            action = payload.get('action') if request.method == 'POST' else None
            error = None
            if action == 'retry' and user['timeouts'] < 2 and user['attempts'] < 2 and not user.get('active') and not user.get('ready'):
                if not user.get('form'):
                    user['form'] = now+60
            elif action == 'id' and user.get('form') and user['timeouts'] < 2:
                value = payload.get('id')
                if isinstance(value, str) and value.strip() and len(value) <= 200:
                    user['ready'] = value.strip()
                    user['form'] = None
                else:
                    error = '请填写 ID（最多 200 字）。'
            elif action == 'start' and user.get('ready') and user['attempts'] < 2 and user['timeouts'] < 2:
                if 'questions' not in user:
                    bank = conn.execute('SELECT questions FROM banks ORDER BY id DESC LIMIT 1').fetchone()
                    if bank:
                        user['questions'] = json.loads(bank['questions'])
                        user['draw'] = secrets.SystemRandom().sample(range(100), 6)
                if 'questions' not in user:
                    error = '题库尚未发布，请稍后再来。'
                else:
                    offset = user['attempts']*3
                    user['attempts'] += 1
                    user['active'] = dict(id=user.pop('ready'), indices=user['draw'][offset:offset+3], index=0, score=0, deadline=now+10)
            elif action == 'answer' and user.get('active'):
                e = user['active']
                if payload.get('number') == e['index']+1 and payload.get('answer') in ['A', 'B', 'C', 'D']:
                    q = user['questions'][e['indices'][e['index']]]
                    e['score'] += int(payload['answer'] == q['answer'])
                    e['index'] += 1
                    e['deadline'] = now+10
                    if e['index'] == 3:
                        user['results'].append(dict(id=e['id'], score=e['score'], time=int(now)))
                        user['active'] = None
            conn.execute('INSERT OR REPLACE INTO users VALUES (?,?)', (uid, json.dumps(user, ensure_ascii=False)))
            output = public(user, now)
            if error:
                output['error'] = error
        response = jsonify(output)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.route('/admin', methods=['GET', 'POST'])
    def admin():
        error = None
        if request.method == 'POST':
            with db() as conn:
                saved = conn.execute("SELECT value FROM settings WHERE key='password'").fetchone()
            if saved and check_password_hash(saved['value'], request.form.get('password', '')):
                session['admin'] = True
                return redirect(url_for('admin'))
            error = '密码错误，或尚未配置 ADMIN_PASSWORD。'
        with db() as conn:
            count = conn.execute('SELECT COUNT(*) FROM banks').fetchone()[0]
            records = []
            if session.get('admin'):
                for row in conn.execute('SELECT data FROM users'):
                    for n, result in enumerate(json.loads(row['data'])['results'], 1):
                        records.append(dict(result, attempt=n))
        return render_template('admin.html', error=error, count=count, records=sorted(records, key=lambda r:r['time'], reverse=True))

    @app.post('/admin/upload')
    @admin_required
    def upload():
        try:
            file = request.files['file']
            reader = csv.DictReader(io.StringIO(file.read().decode('utf-8-sig')))
            questions = []
            if reader.fieldnames != ['question', 'A', 'B', 'C', 'D', 'answer']:
                raise ValueError('表头须为 question,A,B,C,D,answer')
            for row in reader:
                if None in row or any(not (row.get(k) or '').strip() for k in reader.fieldnames):
                    raise ValueError('每一行必须有完整的题目、四个选项和答案')
                answer = row['answer'].strip().upper()
                if answer not in ['A', 'B', 'C', 'D']:
                    raise ValueError('答案必须为 A、B、C 或 D')
                questions.append(dict(question=row['question'].strip(), options=[row[k].strip() for k in 'ABCD'], answer=answer))
            if len(questions) != 100:
                raise ValueError('必须恰好包含 100 道题')
            with db() as conn:
                conn.execute('INSERT INTO banks(questions) VALUES (?)', (json.dumps(questions, ensure_ascii=False),))
        except (ValueError, UnicodeError, KeyError) as exc:
            return str(exc), 400
        return redirect(url_for('admin'))

    @app.get('/admin/template.csv')
    @admin_required
    def template():
        return app.response_class('\ufeffquestion,A,B,C,D,answer\n示例题目,选项一,选项二,选项三,选项四,A\n', mimetype='text/csv', headers={'Content-Disposition':'attachment; filename=questions.csv'})

    @app.post('/admin/logout')
    def logout():
        session.pop('admin', None)
        return redirect(url_for('admin'))

    return app


if __name__ == '__main__':
    create_app().run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
