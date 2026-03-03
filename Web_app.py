from flask import Flask, render_template, request, redirect, url_for, session, flash

# import persistence and scoring from CLI app
from app import load_data, save_data, recommendation_from_percent, PROCESS_TEMPLATES, score_answers
from datetime import datetime
import uuid

app = Flask(__name__)
app.secret_key = 'dev'  # for session cookies during prototype

# helper to retrieve current user or empty dict

def current_user() -> dict:
    return session.get('user', {})


def get_all_records() -> list:
    return load_data()


def find_record(rec_id: str) -> dict | None:
    for r in load_data():
        if r.get('id') == rec_id:
            return r
    return None

@app.route('/')
def home():
    # if no user in session, show a public welcome page inviting login/profile creation
    if 'user' not in session:
        return render_template('welcome.html')
    user = current_user()
    return render_template('landing.html', user_name=user.get('name',''), user_email=user.get('email',''))

@app.route('/user', methods=['GET', 'POST'])
def user_info():
    # custom login: collect the user's name, email, and password and store in session
    if request.method == 'POST':
        name = request.form.get('user_name', '').strip()
        email = request.form.get('user_email', '').strip()
        password = request.form.get('user_password', '')
        password2 = request.form.get('user_password_confirm', '')
        # simple validations
        if not name or not email or '@' not in email or not password:
            error = 'Please provide a valid name, email address, and password.'
            return render_template('user.html', error=error, name=name, email=email)
        if password != password2:
            error = 'Passwords do not match.'
            return render_template('user.html', error=error, name=name, email=email)
        # for prototype store password in session (not secure)
        session['user'] = {'name': name, 'email': email, 'password': password}
        return redirect(url_for('home'))
    return render_template('user.html', name='', email='')

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('user_info'))

@app.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    if 'user' not in session:
        return redirect(url_for('user_info'))
    user = session['user']
    error = None
    success = None
    if request.method == 'POST':
        current = request.form.get('current_password', '')
        new = request.form.get('new_password', '')
        new2 = request.form.get('new_password_confirm', '')
        if current != user.get('password'):
            error = 'Current password is incorrect.'
        elif not new:
            error = 'Please enter a new password.'
        elif new != new2:
            error = 'New passwords do not match.'
        else:
            # update session-stored password
            user['password'] = new
            session['user'] = user
            success = 'Password updated.'
    return render_template('reset_password.html', error=error, success=success)

@app.route('/dashboard')
def dashboard():
    # show history for current user
    if 'user' not in session:
        return redirect(url_for('user_info'))
    user = current_user()
    records = [r for r in get_all_records() if r.get('user', {}).get('email') == user.get('email')]
    return render_template('dashboard.html', records=records)

@app.route('/record/<rec_id>')
def view_record(rec_id):
    rec = find_record(rec_id)
    if not rec:
        return '<h1>Record not found</h1>', 404
    user = current_user()
    if rec.get('user', {}).get('email') != user.get('email'):
        return '<h1>Forbidden</h1>', 403
    percent = rec.get('score', {}).get('percent', 0)
    recommendation = rec.get('score', {}).get('recommendation', '')
    if rec.get('path') == 'quick':
        return render_template('quick_result.html', record=rec, percent=percent, recommendation=recommendation)
    else:
        return render_template('deep_result.html', record=rec, percent=percent, recommendation=recommendation)

@app.route('/record/<rec_id>/edit')
def edit_record(rec_id):
    # dispatch editing based on path
    if 'user' not in session:
        return redirect(url_for('user_info'))
    rec = find_record(rec_id)
    if not rec:
        return '<h1>Record not found</h1>', 404
    if rec.get('user', {}).get('email') != session['user']['email']:
        return '<h1>Forbidden</h1>', 403
    if rec.get('path') == 'quick':
        return redirect(url_for('quick_edit', rec_id=rec_id))
    else:
        return redirect(url_for('deep_edit', rec_id=rec_id))

@app.route('/enter')
def enter():
    # handle the choice from landing page; support guest mode via ?guest=1
    path = request.args.get('path')
    guest = request.args.get('guest')
    # disallow guest deep evaluations: if guest requested deep, redirect to quick start
    if guest == '1' and path == 'deep':
        session.pop('guest_mode', None)
        return redirect(url_for('quick_start'))
    if guest == '1':
        session['guest_mode'] = True
    else:
        session.pop('guest_mode', None)
    if path not in ('quick', 'deep'):
        # invalid or missing choice; redirect back
        return redirect(url_for('home'))
    if path == 'quick':
        return redirect(url_for('quick_start'))
    else:
        return redirect(url_for('deep_evaluation'))

@app.route('/quick', methods=['GET', 'POST'])
def quick_start():
    # initial step: gather name/type/purpose
    error = None
    is_guest = session.get('guest_mode', False)
    if request.method == 'POST':
        name = request.form.get('process_name', '').strip()
        proc_type = request.form.get('process_type')
        purpose = request.form.get('purpose', '').strip()
        missing = []
        if not name:
            missing.append('process name')
        if proc_type not in PROCESS_TEMPLATES:
            missing.append('process type')
        if not purpose:
            missing.append('process purpose')
        if missing:
            error = 'Please provide ' + ', '.join(missing) + '.'
        else:
            session['quick_base'] = {'name': name, 'type': proc_type, 'purpose': purpose}
            return redirect(url_for('quick_details'))
    base = session.get('quick_base', {})
    return render_template('quick_start.html', templates=PROCESS_TEMPLATES,
                           name=base.get('name',''), selected_type=base.get('type',''),
                           purpose=base.get('purpose',''), error=error, is_guest=is_guest)

@app.route('/quick/edit/<rec_id>', methods=['GET', 'POST'])
def quick_edit(rec_id):
    # modify an existing quick record
    if 'user' not in session:
        return redirect(url_for('user_info'))
    rec = find_record(rec_id)
    if not rec:
        return '<h1>Record not found</h1>', 404
    if rec.get('user', {}).get('email') != session['user']['email']:
        return '<h1>Forbidden</h1>', 403
    # GET pre-fill values
    if request.method == 'GET':
        values = {}
        # only supply step value if it exists
        values['step1'] = rec['steps'][0] if rec.get('steps') else ''
        for k,v in rec.get('answers', {}).items():
            values[f'answer_{k}'] = v
        return render_template('quick.html', templates=PROCESS_TEMPLATES,
                               user_name=session['user']['name'], user_email=session['user']['email'],
                               name=rec.get('name'), purpose=rec.get('purpose'),
                               selected_type=rec.get('type'), values=values)
    # POST update
    user_name = session['user'].get('name','')
    user_email = session['user'].get('email','')
    name = request.form.get('process_name', '').strip()
    purpose = request.form.get('purpose', '').strip()
    first_step = request.form.get('step1', '').strip()
    proc_type = request.form.get('process_type')
    missing=[]
    if not name:
        missing.append('process name')
    if proc_type not in PROCESS_TEMPLATES:
        missing.append('process type')
    if not purpose:
        missing.append('process purpose')
    if proc_type != 'R' and not first_step:
        missing.append('first step')
    if missing:
        error='Please provide '+', '.join(missing)+'.'
        return render_template('quick.html', error=error, templates=PROCESS_TEMPLATES,
                               name=name, purpose=purpose, step=first_step,
                               selected_type=proc_type, user_name=user_name, user_email=user_email)
    answers={}
    for q in PROCESS_TEMPLATES[proc_type]['questions']:
        key=q['key']
        raw=request.form.get(f'answer_{key}','').strip()
        try:
            val=int(raw)
        except ValueError:
            val=0
        answers[key]=val
        if val<1 or val>5:
            error='All questions must be answered with a number between 1 and 5.'
            return render_template('quick.html', error=error, templates=PROCESS_TEMPLATES,
                                   name=name, purpose=purpose, step=first_step,
                                   selected_type=proc_type, user_name=user_name, user_email=user_email)
    # update record
    rec.update({
        'name': name,
        'purpose': purpose,
        'type': proc_type,
        'steps': [first_step] if proc_type != 'R' else [],
        'answers':answers
    })
    rec['score']=score_answers(proc_type, answers)
    save_data(get_all_records())
    percent=rec['score']['percent']
    recmd=rec['score']['recommendation']
    return render_template('quick_result.html', record=rec, percent=percent, recommendation=recmd)

@app.route('/quick/details', methods=['GET', 'POST'])
def quick_details():
    # second step: step+questions
    base = session.get('quick_base')
    if not base:
        return redirect(url_for('quick_start'))
    is_guest = session.get('guest_mode', False)
    user_name = session['user'].get('name','') if 'user' in session else ''
    user_email = session['user'].get('email','') if 'user' in session else ''
    if request.method == 'POST':
        first_step = request.form.get('step1', '').strip()
        proc_type = base.get('type')
        missing = []
        # analytics/reporting templates don't need a step description
        if proc_type != 'R' and not first_step:
            missing.append('first step')
        if proc_type not in PROCESS_TEMPLATES:
            missing.append('process type')
        if missing:
            error = 'Please provide ' + ', '.join(missing) + '.'
            return render_template('quick.html', error=error, templates=PROCESS_TEMPLATES,
                                   name=base.get('name'), purpose=base.get('purpose'),
                                   step=first_step, selected_type=proc_type,
                                   user_name=user_name, user_email=user_email)
        answers = {}
        for q in PROCESS_TEMPLATES[proc_type]['questions']:
            key = q['key']
            raw = request.form.get(f'answer_{key}', '').strip()
            try:
                val = int(raw)
            except ValueError:
                val = 0
            answers[key] = val
            if val < 1 or val > 5:
                error = 'All questions must be answered with a number between 1 and 5.'
                return render_template('quick.html', error=error, templates=PROCESS_TEMPLATES,
                                       name=base.get('name'), purpose=base.get('purpose'),
                                       step=first_step, selected_type=proc_type,
                                       user_name=user_name, user_email=user_email)
        score_info = score_answers(proc_type, answers)
        percent = score_info['percent']
        rec = score_info['recommendation']
        record = {
            'id': str(uuid.uuid4()),
            'created': datetime.utcnow().isoformat(),
            'path': 'quick',
            'user': {'name': user_name, 'email': user_email},
            'name': base.get('name'),
            'purpose': base.get('purpose'),
            'type': proc_type,
            # analytics processes may not have steps
            'steps': [first_step] if proc_type != 'R' else [],
            'answers': answers,
            'score': score_info,
        }
        # if not guest, persist; guests see results but nothing is saved
        if not is_guest:
            data = load_data()
            data.append(record)
            save_data(data)
        session.pop('quick_base', None)
        if is_guest:
            session.pop('guest_mode', None)
        return render_template('quick_result.html', record=record, percent=percent, recommendation=rec)
    return render_template('quick.html', templates=PROCESS_TEMPLATES,
                           user_name=user_name, user_email=user_email,
                           name=base.get('name'), purpose=base.get('purpose'),
                           selected_type=base.get('type'), is_guest=is_guest)

@app.route('/deep', methods=['GET', 'POST'])
def deep_evaluation():
    # deeper form with additional descriptive fields; allow guest mode
    # deeper form with additional descriptive fields
    if request.method == 'POST':
        # determine user from session or form
        is_guest = session.get('guest_mode', False)
        if 'user' in session:
            user_name = session['user'].get('name','')
            user_email = session['user'].get('email','')
        else:
            user_name = request.form.get('user_name', '').strip()
            user_email = request.form.get('user_email', '').strip()
        name = request.form.get('process_name', '').strip()
        purpose = request.form.get('purpose', '').strip()
        first_step = request.form.get('step1', '').strip()
        description = request.form.get('description', '').strip()
        step_count = request.form.get('step_count', '').strip()
        proc_type = request.form.get('process_type')
        # debug print
        app.logger.debug(f"Received deep POST: user_name={user_name!r}, user_email={user_email!r}, name={name!r}, purpose={purpose!r}, step={first_step!r}, type={proc_type!r}")
        missing = []
        if not is_guest and not user_name:
            missing.append('your name')
        if not is_guest and not user_email:
            missing.append('your email')
        if not name:
            missing.append('process name')
        if not purpose:
            missing.append('process purpose')
        if not first_step:
            missing.append('first step')
        if not description:
            missing.append('description')
        if not step_count:
            missing.append('number of steps')
        if proc_type not in PROCESS_TEMPLATES:
            missing.append('process type')
        if missing:
            error = 'Please provide ' + ', '.join(missing) + '.'
            return render_template('deep.html', error=error, templates=PROCESS_TEMPLATES,
                                    name=name, purpose=purpose, step=first_step, description=description,
                                    step_count=step_count, selected_type=proc_type,
                                    user_name=user_name, user_email=user_email)
        # gather answers for the chosen template
        answers = {}
        for q in PROCESS_TEMPLATES[proc_type]['questions']:
            key = q['key']
            raw = request.form.get(f'answer_{key}', '').strip()
            try:
                val = int(raw)
            except ValueError:
                val = 0
            answers[key] = val
        score_info = score_answers(proc_type, answers)
        percent = score_info['percent']
        rec = score_info['recommendation']
        record = {
            'id': str(uuid.uuid4()),
            'created': datetime.utcnow().isoformat(),
            'path': 'deep',
            'user': {'name': user_name, 'email': user_email},
            'name': name,
            'purpose': purpose,
            'type': proc_type,
            'steps': [first_step],
            'description': description,
            'step_count': step_count,
            'answers': answers,
            'score': score_info,
        }
        # persist only for logged-in users; guest evaluations are ephemeral
        if not session.get('guest_mode', False):
            data = load_data()
            data.append(record)
            save_data(data)
            return render_template('deep_result.html', record=record, percent=percent, recommendation=rec)
        else:
            # clear guest mode after showing results
            session.pop('guest_mode', None)
            return render_template('deep_result.html', record=record, percent=percent, recommendation=rec)
    # include session user if available
    user = session.get('user', {})
    return render_template('deep.html', templates=PROCESS_TEMPLATES,
                           user_name=user.get('name',''),
                           user_email=user.get('email',''),
                           is_guest=session.get('guest_mode', False))

if __name__ == '__main__':
    # run the development server when executed directly
    # allow PORT env var override (ports like 5000 may be occupied on macOS)
    import os
    port = int(os.environ.get('PORT', 5000))
    print(f"Starting development server on http://127.0.0.1:{port}/")
    app.run(debug=True, port=port)