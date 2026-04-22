from flask import Flask, jsonify, render_template, request, redirect, url_for, session, flash, send_file
from werkzeug.security import generate_password_hash, check_password_hash

# import persistence and scoring from CLI app
from app import load_data, save_data, recommendation_from_percent, PROCESS_TEMPLATES, score_answers
from db import create_feedback_entry, get_admin_snapshot, init_database, load_users as db_load_users, save_users as db_save_users
from api import api_bp
from datetime import datetime, timedelta, timezone
import json
import os
import re
import secrets
import smtplib
import string
from io import BytesIO
import urllib.error
import urllib.parse
import urllib.request
import ssl
from email.message import EmailMessage
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', '').strip() or secrets.token_urlsafe(32)
secure_cookies = os.environ.get('SESSION_COOKIE_SECURE', '0').strip() == '1'
app.config.update(
    SESSION_PERMANENT=False,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=1),
    SESSION_REFRESH_EACH_REQUEST=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=secure_cookies,
    TEMPLATES_AUTO_RELOAD=True,
)
app.jinja_env.auto_reload = True
init_database()

# Register API blueprint
app.register_blueprint(api_bp)

current_app_mode = os.environ.get('APP_ENV', '').strip().lower() or 'development'
print(f'Process Triage starting in {current_app_mode} mode')

SOFT_DELETE_RETENTION_DAYS = 730
EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')
MIN_PASSWORD_LENGTH = 10
ENFORCE_MISTRAL_LIVE = os.environ.get('ENFORCE_MISTRAL_LIVE', '1').strip() != '0'


@app.template_filter('pretty_date')
def pretty_date(value: str) -> str:
    if not value:
        return 'N/A'
    try:
        return datetime.fromisoformat(value).strftime('%B %d, %Y')
    except (TypeError, ValueError):
        return str(value)[:10]


@app.template_filter('process_type_label')
def process_type_label(value: str) -> str:
    if not value:
        return 'N/A'
    tpl = PROCESS_TEMPLATES.get(value)
    if tpl and isinstance(tpl, dict):
        return tpl.get('label', value)
    return value


def full_name(first_name: str, last_name: str) -> str:
    return f"{first_name} {last_name}".strip()


def normalize_email(email: str) -> str:
    return str(email or '').strip().lower()


def is_valid_email(email: str) -> bool:
    candidate = normalize_email(email)
    return bool(candidate and EMAIL_RE.fullmatch(candidate))


def password_strength_error(password: str) -> str | None:
    value = str(password or '')
    if len(value) < MIN_PASSWORD_LENGTH:
        return f'Password must be at least {MIN_PASSWORD_LENGTH} characters.'
    if not re.search(r'[A-Z]', value):
        return 'Password must include at least one uppercase letter.'
    if not re.search(r'[a-z]', value):
        return 'Password must include at least one lowercase letter.'
    if not re.search(r'\d', value):
        return 'Password must include at least one number.'
    return None


def running_in_production() -> bool:
    explicit_env = os.environ.get('APP_ENV', '').strip().lower() or os.environ.get('FLASK_ENV', '').strip().lower()
    if explicit_env:
        return explicit_env == 'production'
    return False


def startup_security_validation() -> None:
    if not running_in_production():
        return

    errors: list[str] = []

    secret_key = os.environ.get('FLASK_SECRET_KEY', '').strip()
    if not secret_key:
        errors.append('FLASK_SECRET_KEY is required in production.')
    elif len(secret_key) < 32:
        errors.append('FLASK_SECRET_KEY must be at least 32 characters in production.')
    elif 'replace-with' in secret_key.lower() or 'changeme' in secret_key.lower():
        errors.append('FLASK_SECRET_KEY appears to be a placeholder and must be replaced.')

    if os.environ.get('SESSION_COOKIE_SECURE', '0').strip() != '1':
        errors.append('SESSION_COOKIE_SECURE must be set to 1 in production.')

    admin_email = os.environ.get('ADMIN_LOGIN_EMAIL', '').strip().lower()
    admin_password = os.environ.get('ADMIN_LOGIN_PASSWORD', '').strip()

    if bool(admin_email) != bool(admin_password):
        errors.append('ADMIN_LOGIN_EMAIL and ADMIN_LOGIN_PASSWORD must both be set together or both be omitted.')

    if admin_email and not is_valid_email(admin_email):
        errors.append('ADMIN_LOGIN_EMAIL is not a valid email address.')

    if admin_password:
        pwd_error = password_strength_error(admin_password)
        if pwd_error:
            errors.append(f'ADMIN_LOGIN_PASSWORD is too weak: {pwd_error}')

    if errors:
        details = '\n'.join(f'- {item}' for item in errors)
        raise RuntimeError('Startup security validation failed:\n' + details)


startup_security_validation()


def first_name_from_user(user: dict) -> str:
    if user.get('first_name'):
        return user.get('first_name', '')
    name = user.get('name', '')
    if not name:
        return ''
    return str(name).strip().split(' ')[0]


def load_users() -> list:
    try:
        data = db_load_users()
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_users(users: list) -> None:
    try:
        db_save_users(users)
    except Exception:
        pass


def find_user_by_email(email: str) -> dict | None:
    target = normalize_email(email)
    if not target:
        return None
    for user in load_users():
        if normalize_email(user.get('email', '')) == target:
            return user
    return None


def create_user_account(first_name: str, last_name: str, email: str, password: str) -> tuple[bool, str]:
    users = load_users()
    email_norm = normalize_email(email)
    if not is_valid_email(email_norm):
        return False, 'Please provide a valid email address.'
    pwd_error = password_strength_error(password)
    if pwd_error:
        return False, pwd_error
    if any(normalize_email(u.get('email', '')) == email_norm for u in users):
        return False, 'An account with this email already exists. Please sign in.'
    users.append({
        'first_name': first_name,
        'last_name': last_name,
        'name': full_name(first_name, last_name),
        'email': email_norm,
        'password_hash': generate_password_hash(password),
        'created': current_date_string(),
    })
    save_users(users)
    return True, ''


def update_user_password(email: str, new_password: str) -> bool:
    if not email:
        return False
    if password_strength_error(new_password):
        return False
    users = load_users()
    email_norm = normalize_email(email)
    updated = False
    for user in users:
        if normalize_email(user.get('email', '')) == email_norm:
            user['password_hash'] = generate_password_hash(new_password)
            updated = True
            break
    if updated:
        save_users(users)
    return updated


def password_matches(stored_hash: str, candidate_password: str) -> bool:
    try:
        return bool(check_password_hash(str(stored_hash or ''), str(candidate_password or '')))
    except (TypeError, ValueError):
        # Treat malformed legacy hashes as non-matching instead of raising a 500.
        return False


def generate_reset_code(length: int = 6) -> str:
    return ''.join(secrets.choice(string.digits) for _ in range(length))


def send_password_reset_code(email: str, code: str) -> bool:
    smtp_host = os.environ.get('SMTP_HOST', '').strip()
    smtp_port = int(os.environ.get('SMTP_PORT', '587'))
    smtp_user = os.environ.get('SMTP_USER', '').strip()
    smtp_pass = os.environ.get('SMTP_PASS', '').strip()
    smtp_from = os.environ.get('SMTP_FROM', smtp_user).strip()

    if not smtp_host or not smtp_user or not smtp_pass or not smtp_from:
        return False

    msg = EmailMessage()
    msg['Subject'] = 'Your Process Triage password reset code'
    msg['From'] = smtp_from
    msg['To'] = email
    msg.set_content(
        'We received a request to reset your Process Triage password.\n\n'
        f'Your verification code is: {code}\n\n'
        'This code expires in 15 minutes.\n'
        'If you did not request this, you can ignore this email.'
    )

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException, TimeoutError, ValueError):
        return False


def bootstrap_admin_credentials() -> tuple[str, str]:
    email = os.environ.get('ADMIN_LOGIN_EMAIL', '').strip().lower()
    password = os.environ.get('ADMIN_LOGIN_PASSWORD', '').strip()
    return email, password


def ensure_bootstrap_admin_account() -> str:
    admin_email, admin_password = bootstrap_admin_credentials()
    if not admin_email or not admin_password:
        return ''
    if not is_valid_email(admin_email):
        return ''
    if password_strength_error(admin_password):
        return ''
    existing = find_user_by_email(admin_email)
    if not existing:
        create_user_account('System', 'Admin', admin_email, admin_password)
    return admin_email


def is_admin_email(email: str) -> bool:
    target = str(email or '').strip().lower()
    if not target:
        return False
    admin_emails = {
        item.strip().lower()
        for item in os.environ.get('ADMIN_EMAILS', '').split(',')
        if item.strip()
    }
    bootstrap_email, _ = bootstrap_admin_credentials()
    if bootstrap_email:
        admin_emails.add(bootstrap_email)
    return target in admin_emails


def login_session_from_user(user: dict) -> None:
    session.clear()
    session['user'] = {
        'id': user.get('id'),
        'first_name': user.get('first_name', ''),
        'last_name': user.get('last_name', ''),
        'name': user.get('name', full_name(user.get('first_name', ''), user.get('last_name', ''))),
        'email': user.get('email', ''),
    }


def record_user_payload(user: dict) -> dict:
    return {
        'id': user.get('id'),
        'first_name': user.get('first_name', ''),
        'last_name': user.get('last_name', ''),
        'name': user.get('name', full_name(user.get('first_name', ''), user.get('last_name', ''))),
        'email': user.get('email', ''),
    }

# helper to retrieve current user or empty dict

def current_user() -> dict:
    return session.get('user', {})


def persist_guest_result_for_user(user: dict) -> bool:
    guest_result = session.get('guest_result')
    if not isinstance(guest_result, dict):
        return False

    path = str(guest_result.get('path', '')).strip().lower()
    if path not in ('quick', 'deep'):
        return False

    payload = {
        'name': guest_result.get('name', ''),
        'purpose': guest_result.get('purpose', ''),
        'type': guest_result.get('type', ''),
        'steps': guest_result.get('steps', []),
        'description': guest_result.get('description', ''),
        'answers': guest_result.get('answers', {}),
        'score': guest_result.get('score'),
        'status': 'submitted',
    }
    upsert_partial_record(user, path, payload)
    session.pop('guest_result', None)
    session.pop('guest_mode', None)
    return True


def get_all_records() -> list:
    purge_expired_user_deleted_records()
    return load_data()


def get_user_records(email: str, include_deleted: bool = False) -> list:
    if not email:
        return []
    user_records = [r for r in load_data() if r.get('user', {}).get('email') == email]
    if include_deleted:
        return user_records
    return [r for r in user_records if not is_soft_deleted_record(r)]


def parse_utc_datetime(raw_value: str) -> datetime | None:
    value = str(raw_value or '').strip()
    if not value:
        return None
    normalized = value.replace('Z', '+00:00')
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc)
        return parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.strptime(value[:10], '%Y-%m-%d').replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def retention_delete_date_from_timestamp(raw_value: str) -> str:
    base = parse_utc_datetime(raw_value)
    if not base:
        base = utc_now()
    return (base + timedelta(days=SOFT_DELETE_RETENTION_DAYS)).date().isoformat()


def is_soft_deleted_record(record: dict) -> bool:
    return bool(str(record.get('deleted_at', '')).strip())


def purge_expired_user_deleted_records() -> int:
    data = load_data()
    now = utc_now()
    kept_records = []
    purged_count = 0

    for item in data:
        deleted_at = str(item.get('deleted_at', '')).strip()
        deleted_by = str(item.get('deleted_by', '')).strip().lower()
        if deleted_at and deleted_by == 'user':
            deleted_on = parse_utc_datetime(deleted_at)
            if deleted_on and deleted_on + timedelta(days=SOFT_DELETE_RETENTION_DAYS) <= now:
                purged_count += 1
                continue
        kept_records.append(item)

    if purged_count > 0:
        save_data(kept_records)
    return purged_count


def next_submission_id(records: list | None = None) -> str:
    if records is None:
        records = load_data()
    max_num = 0
    for rec in records:
        rec_id = str(rec.get('id', ''))
        if rec_id.startswith('S') and rec_id[1:].isdigit():
            max_num = max(max_num, int(rec_id[1:]))
    return f"S{max_num + 1:03d}"


def current_date_string() -> str:
    return utc_now().date().isoformat()


def upsert_partial_record(user: dict, path: str, payload: dict, rec_id: str | None = None) -> str:
    data = load_data()
    email = user.get('email', '')
    if not email:
        return ''

    existing = None
    if rec_id:
        for item in data:
            if item.get('id') == rec_id and item.get('user', {}).get('email') == email:
                existing = item
                break

    if existing:
        existing.update(payload)
        existing['updated'] = current_date_string()
        existing['status'] = payload.get('status', existing.get('status', 'partial'))
        record_id = existing.get('id', rec_id)
    else:
        record_id = rec_id or next_submission_id(data)
        new_record = {
            'id': record_id,
            'created': current_date_string(),
            'updated': current_date_string(),
            'path': path,
            'status': payload.get('status', 'partial'),
            'user': record_user_payload(user),
        }
        new_record.update(payload)
        data.append(new_record)

    save_data(data)
    return record_id


def find_record(rec_id: str, include_deleted: bool = False) -> dict | None:
    purge_expired_user_deleted_records()
    for r in load_data():
        if r.get('id') == rec_id:
            if not include_deleted and is_soft_deleted_record(r):
                return None
            return r
    return None


def delete_user_record(rec_id: str | None, email: str) -> bool:
    if not rec_id or not email:
        return False
    data = load_data()
    updated = False
    for item in data:
        if item.get('id') == rec_id and item.get('user', {}).get('email') == email:
            if is_soft_deleted_record(item):
                return False
            item['deleted_at'] = utc_now().isoformat()
            item['deleted_by'] = 'user'
            item['hard_delete_after'] = retention_delete_date_from_timestamp(item['deleted_at'])
            item['updated'] = current_date_string()
            updated = True
            break
    if not updated:
        return False
    save_data(data)
    return True


def restore_user_record(rec_id: str | None, email: str) -> bool:
    if not rec_id or not email:
        return False
    data = load_data()
    restored = False
    for item in data:
        if item.get('id') == rec_id and item.get('user', {}).get('email') == email:
            if not is_soft_deleted_record(item):
                return False
            item.pop('deleted_at', None)
            item.pop('deleted_by', None)
            item.pop('hard_delete_after', None)
            item['updated'] = current_date_string()
            restored = True
            break
    if not restored:
        return False
    save_data(data)
    return True


def suggestion_summary(record: dict) -> str:
    score = record.get('score', {}) if isinstance(record.get('score'), dict) else {}
    recommendation = score.get('recommendation') or record.get('recommendation') or 'No recommendation yet.'
    percent = score.get('percent')
    score_text = f"{round(float(percent), 1)}%" if percent is not None else 'N/A'
    return f"Current recommendation: {recommendation}. Score: {score_text}."


def get_llm_runtime_config() -> dict | None:
    mistral_key = os.environ.get('MISTRAL_API_KEY', '').strip()
    if mistral_key:
        return {
            'provider': 'mistral',
            'api_key': mistral_key,
            'model': os.environ.get('MISTRAL_MODEL', 'mistral-large-latest').strip() or 'mistral-large-latest',
            'chat_endpoint': 'https://api.mistral.ai/v1/chat/completions',
        }

    return None


def get_mistral_runtime_config() -> dict | None:
    mistral_key = os.environ.get('MISTRAL_API_KEY', '').strip()
    if not mistral_key:
        return None
    return {
        'api_key': mistral_key,
        'model': os.environ.get('MISTRAL_MODEL', 'mistral-large-latest').strip() or 'mistral-large-latest',
        'chat_endpoint': 'https://api.mistral.ai/v1/chat/completions',
    }


def outbound_ssl_context() -> ssl.SSLContext | None:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None





def call_external_llm(record: dict, user_message: str, chat_history: list | None = None, deep_dive: bool = False) -> str | None:
    llm_config = get_llm_runtime_config()
    if not llm_config:
        app.logger.warning('Mistral live mode is enabled but MISTRAL_API_KEY is missing.')
        return None

    if deep_dive:
        system_prompt = (
            'You are Triage Assistant for Process Triage deep dives.\n\n'
            'PERSONA:\n'
            '- Act like a seasoned business process analyst: practical, specific, and outcome-focused.\n'
            '- Use plain language a non-technical stakeholder can understand.\n\n'
            'RULES:\n'
            '1) Ask clarifying questions first. Do not provide recommendations until you have collected enough information.\n'
            '2) Ask at least 6 clarifying questions before any recommendations, unless the user has already provided equivalent detail.\n'
            '3) Ask questions that reveal: goal/scope, start/end triggers, step-by-step workflow, volume/frequency, tools/systems, pain points, exceptions, risks/compliance, stakeholders/handoffs, success metrics.\n'
            '4) Treat the user-provided main steps as the backbone order. Keep those main steps stable unless the user explicitly edits them.\n'
            '5) Do not jump to the next main step immediately after a short answer. Ask at least one context-driven clarifying question for the current step unless the user explicitly says to move on.\n'
            '6) Before recommendations, build the process map with the user. Summarize discovered steps and sub-steps as you go.\n'
            '7) When a step includes mini-steps/sub-steps, capture them as child steps below that main step in the process map.\n'
            '8) If information is missing, continue asking questions rather than guessing.\n'
            '9) Ask only ONE question per message. Do not ask multiple questions at once. Wait for the user response before the next question.\n'
            '10) Do not include scripted option menus; continue naturally based on the user response.\n'
            '11) After enough detail is gathered, provide specific, easy-to-understand improvement suggestions in ordered bullets.\n'
            '12) Keep each suggestion concrete (what to change, where in the process, expected benefit).\n'
            '13) If the user asks for recommendations OR says they do not know/no longer have details, STOP asking questions and give recommendations immediately from known information.\n'
            '14) Do not tell the user to review prior discussion; treat the current process map as the source of truth unless the user explicitly asks for a review.\n'
            '15) If a review is explicitly requested, format it as: numbered main steps with micro-steps as bullet points under each step.\n'
            '16) Keep response formatting easy to scan: short sections, numbered recommendations, and plain language.'
        )
    else:
        system_prompt = (
            'You are helping a user improve a process workflow. Keep answers practical and concise. '
            'Use the process details and recommendation context when relevant.'
        )

    llm_messages = [
        {
            'role': 'system',
            'content': system_prompt,
        },
        {
            'role': 'user',
            'content': (
                f"Process: {record.get('name', '')}\n"
                f"Purpose: {record.get('purpose', '')}\n"
                f"Type: {record.get('type', '')}\n"
                f"Recommendation Context: {suggestion_summary(record)}"
            ),
        },
    ]

    if chat_history:
        # Keep recent conversational context to help the model track what has already been asked/answered.
        for msg in chat_history[-12:]:
            role = msg.get('role')
            content = str(msg.get('content', '')).strip()
            if role in ('user', 'assistant') and content:
                llm_messages.append({'role': role, 'content': content})

    # Avoid duplicating the same user turn when the caller already appended it to chat_history.
    append_user_turn = True
    if llm_messages:
        last = llm_messages[-1]
        if last.get('role') == 'user' and str(last.get('content', '')).strip() == str(user_message or '').strip():
            append_user_turn = False

    if append_user_turn:
        llm_messages.append({'role': 'user', 'content': user_message})

    try:
        payload = {
            'model': llm_config['model'],
            'messages': llm_messages,
            'temperature': 0.4,
        }
        req = urllib.request.Request(
            llm_config['chat_endpoint'],
            data=json.dumps(payload).encode('utf-8'),
            headers={
                'Content-Type': 'application/json',
                'Authorization': f"Bearer {llm_config['api_key']}",
            },
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=20, context=outbound_ssl_context()) as resp:
            body = json.loads(resp.read().decode('utf-8'))
        return body.get('choices', [{}])[0].get('message', {}).get('content', '').strip() or None
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError, TimeoutError) as exc:
        app.logger.warning('External LLM chat call failed (provider=%s, model=%s): %s', llm_config.get('provider'), llm_config.get('model'), exc)
        return None


def llm_reply(record: dict, user_message: str, chat_history: list | None = None, deep_dive: bool = False) -> str:
    external = call_external_llm(record, user_message, chat_history=chat_history, deep_dive=deep_dive)
    if external:
        if deep_dive:
            cleaned = _strip_existing_navigation_hint(external)
            if _wants_recommendations_now(user_message):
                if _assistant_still_asking_questions(cleaned) or not _has_recommendation_signals(cleaned):
                    return deep_dive_recommendation_response(record, user_message)
                if _wants_structured_review(user_message):
                    return deep_dive_recommendation_response(record, user_message)
            return cleaned
        return external

    if ENFORCE_MISTRAL_LIVE:
        base = (
            'Mistral is currently unavailable or not configured. '
            'Please verify MISTRAL_API_KEY and try again.'
        )
        if deep_dive:
            return base
        return base

    if deep_dive:
        return _strip_existing_navigation_hint(deep_dive_fallback_reply(record, user_message, chat_history=chat_history))

    lowered = user_message.lower()
    if any(term in lowered for term in ['next step', 'next steps', 'what should i do', 'plan']):
        return (
            'A practical next step is to map the current process in 5-7 bullets, identify the highest-friction handoff, '
            'and run a 1-week pilot with one improvement. ' + suggestion_summary(record)
        )
    if any(term in lowered for term in ['automate', 'automation', 'llm']):
        return (
            'Good automation candidates are repetitive steps with clear inputs/outputs and predictable rules. '
            'If exceptions are common, document exception paths first before automating.'
        )
    if any(term in lowered for term in ['risk', 'compliance', 'error']):
        return (
            'Start by defining the top 3 failure modes, owner for each control, and where quality checks should happen. '
            'Reducing ambiguity in handoffs often lowers both risk and rework.'
        )
    return (
        'Thanks, that context helps. I would prioritize one narrow change with measurable impact, then expand after validating results. '
        + suggestion_summary(record)
    )


def deep_dive_has_recommendations(messages: list) -> bool:
    if not isinstance(messages, list):
        return False
    recommendation_markers = (
        'recommendation',
        'recommendations',
        'i recommend',
        'suggested actions',
        'next steps',
    )
    for msg in messages:
        if msg.get('role') != 'assistant':
            continue
        content = str(msg.get('content', '')).lower()
        if any(marker in content for marker in recommendation_markers):
            return True
    return False


def _wants_recommendations_now(text: str) -> bool:
    lowered = str(text or '').strip().lower()
    if not lowered:
        return False
    triggers = (
        'recommend',
        'recommendation',
        'improve',
        'improvement',
        'next steps',
        'action items',
        "don't know",
        'do not know',
        'no more info',
        'no more information',
        'all i can',
        "that's all",
        'that is all',
        'enough info',
        'enough information',
    )
    return any(token in lowered for token in triggers)


def _wants_structured_review(text: str) -> bool:
    lowered = str(text or '').strip().lower()
    markers = ('review', 'recap', 'summarize', 'summary', 'what we discussed', 'what we have discussed')
    return any(marker in lowered for marker in markers)


def _assistant_still_asking_questions(text: str) -> bool:
    lowered = str(text or '').strip().lower()
    if not lowered:
        return False
    question_starters = (
        'what ', 'which ', 'who ', 'when ', 'where ', 'why ', 'how ',
        'can you', 'could you', 'please share', 'tell me',
    )
    return ('?' in lowered) or any(lowered.startswith(starter) for starter in question_starters)


def _has_recommendation_signals(text: str) -> bool:
    lowered = str(text or '').strip().lower()
    markers = ('recommend', 'next steps', 'action', 'improve', 'should', 'suggest')
    return any(marker in lowered for marker in markers)


def _process_map_main_and_micro(record: dict) -> tuple[list[dict], dict[str, list[dict]]]:
    process_map = record.get('process_map', {}) if isinstance(record.get('process_map', {}), dict) else {}
    steps = process_map.get('steps', []) if isinstance(process_map.get('steps', []), list) else []

    main_steps: list[dict] = []
    micro_by_parent: dict[str, list[dict]] = {}

    for item in steps:
        if not isinstance(item, dict):
            continue
        lane = str(item.get('lane', 'main')).strip().lower()
        if lane == 'main':
            main_steps.append(item)
            sid = str(item.get('id', '')).strip()
            if sid and sid not in micro_by_parent:
                micro_by_parent[sid] = []

    for item in steps:
        if not isinstance(item, dict):
            continue
        lane = str(item.get('lane', 'main')).strip().lower()
        if lane == 'below':
            parent = str(item.get('parallel_of', '')).strip()
            if parent:
                micro_by_parent.setdefault(parent, []).append(item)

    return main_steps, micro_by_parent


def deep_dive_structured_review(record: dict) -> str:
    main_steps, micro_by_parent = _process_map_main_and_micro(record)
    if not main_steps:
        return 'Structured Process Review\n1) Process map not available yet.'

    lines = ['Structured Process Review']
    for idx, main in enumerate(main_steps, start=1):
        main_text = _clean_step_text(main.get('text', '')) or f'Main step {idx}'
        lines.append(f'{idx}) {main_text}')
        sid = str(main.get('id', '')).strip()
        micros = micro_by_parent.get(sid, [])
        for micro in micros:
            micro_text = _clean_step_text(micro.get('text', ''))
            if micro_text:
                lines.append(f'   - {micro_text}')
    return '\n'.join(lines)


def deep_dive_recommendation_response(record: dict, user_message: str) -> str:
    main_steps, micro_by_parent = _process_map_main_and_micro(record)
    wants_review = _wants_structured_review(user_message)

    lines: list[str] = []
    if wants_review:
        lines.append(deep_dive_structured_review(record))
        lines.append('')

    lines.append('Recommended Improvements (Prioritized)')

    if not main_steps:
        lines.append('1) Standardize inputs and ownership for the first workflow step to reduce rework and delays.')
        lines.append('2) Add a simple SLA/checkpoint for handoffs so bottlenecks are visible early.')
        lines.append('3) Track one outcome metric weekly (cycle time or error rate) to confirm improvements are working.')
        return '\n'.join(lines)

    rec_idx = 1
    for main in main_steps[:4]:
        sid = str(main.get('id', '')).strip()
        step_text = _clean_step_text(main.get('text', '')) or 'this step'
        micros = micro_by_parent.get(sid, [])
        ref_id = sid or 'S?'

        if micros:
            lines.append(
                f"{rec_idx}) [R{rec_idx} -> {ref_id}] For '{step_text}', convert the repeated micro-steps into a short checklist/template so execution is consistent each time (expected benefit: fewer misses and less rework)."
            )
        else:
            lines.append(
                f"{rec_idx}) [R{rec_idx} -> {ref_id}] For '{step_text}', define required input fields and a clear completion definition before work starts (expected benefit: faster throughput and fewer back-and-forth loops)."
            )
        rec_idx += 1

    lines.append(
        f"{rec_idx}) [R{rec_idx} -> CROSS-STEP] Add a handoff notification and SLA timer between major steps to surface delays quickly (expected benefit: reduced idle time and clearer accountability)."
    )
    rec_idx += 1
    lines.append(
        f"{rec_idx}) [R{rec_idx} -> HIGHEST-FRICTION] Pilot one automation candidate in the highest-friction step first (e.g., auto-routing, status updates, or data validation) and measure cycle-time impact for 2 weeks."
    )

    return '\n'.join(lines)


def _is_affirmation_message(text: str) -> bool:
    normalized = str(text or '').strip().lower()
    if not normalized:
        return False
    return normalized in {
        'yes', 'y', 'ok', 'okay', 'sure', 'sounds good', 'let us proceed', 'lets proceed',
        'continue', 'go ahead', 'ready', 'yes please', 'proceed'
    }


def _extract_step_numbers(text: str) -> list[int]:
    values: list[int] = []
    for match in re.findall(r'\bstep\s*(\d{1,2})\b', str(text or '').lower()):
        try:
            num = int(match)
        except ValueError:
            continue
        if 1 <= num <= 99:
            values.append(num)
    return values


def infer_next_deep_step(chat_history: list | None) -> int:
    if not isinstance(chat_history, list):
        return 1

    user_texts = [str(m.get('content', '')).strip() for m in chat_history if m.get('role') == 'user']
    explicit_steps: list[int] = []
    for text in user_texts:
        explicit_steps.extend(_extract_step_numbers(text))
    if explicit_steps:
        return max(explicit_steps) + 1

    meaningful = [
        text for text in user_texts
        if len(text) >= 8 and not _is_affirmation_message(text)
    ]
    if not meaningful:
        return 1
    return len(meaningful) + 1


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = str(text or '').lower()
    return any(term in lowered for term in terms)


def deep_dive_probe_for_message(user_message: str, step_number: int) -> str:
    text = str(user_message or '').strip().lower()

    if not _contains_any(text, ('form', 'email', 'ticket', 'portal', 'api', 'phone', 'spreadsheet')):
        return 'what input channel/tool is used (form, email, ticket, portal, API, phone, or spreadsheet)?'
    if not _contains_any(text, ('minute', 'minutes', 'hour', 'hours', 'day', 'days', 'sla', 'turnaround', 'deadline')):
        return 'how long does this step usually take, and is there an SLA/deadline?'
    if not _contains_any(text, ('exception', 'rework', 'error', 'reject', 'returned', 'blocked')):
        return 'what common exceptions or rework happen in this step?'
    if not _contains_any(text, ('approve', 'approval', 'review', 'sign-off', 'manager', 'authorized')):
        return 'does this step require approval or review, and by whom?'
    if not _contains_any(text, ('output', 'handoff', 'deliverable', 'result', 'send', 'notify')):
        return 'what is the output of this step, and who receives it next?'

    followups = [
        'what data fields are required to complete this step?',
        'is this step ever done in parallel with another activity?',
        'what quality check confirms this step is complete?',
        'what is the most frequent bottleneck in this step?',
    ]
    return followups[(max(step_number, 1) - 1) % len(followups)]


def deep_dive_missing_detail_probe(user_message: str) -> str | None:
    text = str(user_message or '').strip().lower()
    if not text:
        return None

    if not _contains_any(text, ('form', 'email', 'ticket', 'portal', 'api', 'phone', 'spreadsheet', 'system', 'tool')):
        return 'what method or tool is used to execute this step?'
    if not _contains_any(text, ('minute', 'minutes', 'hour', 'hours', 'day', 'days', 'sla', 'turnaround', 'deadline', 'time')):
        return 'how long does this step usually take?'
    if not _contains_any(text, ('sub-step', 'sub step', 'mini-step', 'mini step', 'then', 'after', 'before', 'first', 'second', 'third', 'review', 'check', 'handoff')):
        return 'can you break this into mini-steps or checkpoints within this step?'
    return None


def _is_drill_down_request(text: str) -> bool:
    return _contains_any(
        text,
        (
            'drill',
            'drill down',
            'go deeper',
            'deeper',
            'expand',
            'more detail',
            'details',
            'elaborate',
        ),
    )


def _is_explicit_next_step_request(text: str) -> bool:
    normalized = str(text or '').strip().lower()
    return _contains_any(
        normalized,
        (
            'next step',
            'continue',
            'move on',
            'go to step',
            'step 2',
            'step 3',
            'proceed',
        ),
    )


def _deep_dive_navigation_hint(current_step: int, next_step: int) -> str:
    cur = max(current_step, 1)
    nxt = max(next_step, cur + 1)
    return (
        f' Options for your next reply: "Next step" (move to step {nxt}) '
        f'or "Drill down step {cur}" (add detail to step {cur}).'
    )


def _strip_existing_navigation_hint(reply: str) -> str:
    text = str(reply or '').strip()
    marker = 'options for your next reply:'
    idx = text.lower().find(marker)
    if idx == -1:
        return text
    return text[:idx].rstrip()


def deep_dive_enforce_navigation_options(reply: str, user_message: str, chat_history: list | None = None) -> str:
    base_reply = _strip_existing_navigation_hint(reply)
    next_step = infer_next_deep_step(chat_history)

    if next_step <= 1:
        return base_reply + _deep_dive_navigation_hint(1, 2)

    previous_step = max(next_step - 1, 1)
    requested_steps = _extract_step_numbers(user_message)
    drill_target = requested_steps[-1] if requested_steps else previous_step
    drill_target = max(1, min(drill_target, previous_step))

    if _is_drill_down_request(user_message):
        return base_reply + _deep_dive_navigation_hint(drill_target, previous_step + 1)

    return base_reply + _deep_dive_navigation_hint(previous_step, next_step)


def deep_dive_fallback_reply(record: dict, user_message: str, chat_history: list | None = None) -> str:
    if _wants_recommendations_now(user_message):
        return deep_dive_recommendation_response(record, user_message)

    next_step = infer_next_deep_step(chat_history)
    if next_step <= 1:
        return (
            'Thanks. Let us build your process map first. What is step 1, who performs it, '
            'how does it arrive (form, email, ticket, API, or phone), and what triggers it to begin?'
        )

    previous_step = max(next_step - 1, 1)
    requested_steps = _extract_step_numbers(user_message)
    drill_target = requested_steps[-1] if requested_steps else previous_step
    drill_target = max(1, min(drill_target, previous_step))

    if _is_drill_down_request(user_message):
        probe = deep_dive_probe_for_message(user_message, drill_target)
        return (
            f'Great, let us drill down into step {drill_target}. '
            f'For this step, {probe}'
        )

    missing_probe = deep_dive_missing_detail_probe(user_message)
    if missing_probe and not _is_explicit_next_step_request(user_message):
        return (
            f'Thanks, that helps map step {previous_step}. Before we move to step {next_step}, '
            f'for step {previous_step}, {missing_probe}'
        )

    return (
        f'Thanks, that helps map step {previous_step}. What is step {next_step}, including who performs it, '
        f'how it starts, and what output it produces?'
    )


def rewind_deep_dive_messages(messages: list) -> list:
    if not isinstance(messages, list):
        return []

    safe_messages = [msg for msg in messages if isinstance(msg, dict)]
    if len(safe_messages) <= 1:
        return safe_messages
    if len(safe_messages) <= 2:
        return safe_messages[:1]
    return safe_messages[:-2]


def _clean_step_text(text: str) -> str:
    cleaned = re.sub(r'\s+', ' ', str(text or '')).strip()
    # Keep full semantic detail for LLM summarization while preventing unbounded payloads.
    return cleaned[:500]


def _simplify_step_label(text: str, max_chars: int = 50) -> str:
    cleaned = _clean_step_text(text)
    if len(cleaned) <= max_chars:
        return cleaned

    # Prefer a short phrase split by common separators before hard truncation.
    for separator in (':', ';', ',', ' - ', ' then ', ' and '):
        if separator in cleaned:
            head = cleaned.split(separator, 1)[0].strip()
            if 8 <= len(head) <= max_chars:
                return head

    clipped = cleaned[:max_chars].rstrip()
    if len(clipped) < len(cleaned):
        return clipped + '...'
    return clipped


def parse_process_description_steps(description: str, max_steps: int = 14) -> list[str]:
    text = str(description or '').strip()
    if not text:
        return []

    lines = [line.strip() for line in re.split(r'\n+', text) if line.strip()]
    segments: list[str] = []

    if len(lines) > 1:
        segments = lines
    else:
        separators = r';|\.|\u2022|\u2023|\u25E6|\u2043|\u2219|\bthen\b|\s->\s|\s=>\s'
        segments = [part.strip() for part in re.split(separators, text, flags=re.IGNORECASE) if part.strip()]

    normalized: list[str] = []
    seen: set[str] = set()

    for raw in segments:
        cleaned = re.sub(r'^\s*(?:step\s*)?\d+\s*[\).:\-]\s*', '', raw, flags=re.IGNORECASE)
        cleaned = re.sub(r'^\s*[-*]+\s*', '', cleaned)
        cleaned = _clean_step_text(cleaned)
        if len(cleaned) < 5:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(cleaned)
        if len(normalized) >= max_steps:
            break

    if normalized:
        return normalized
    return [_clean_step_text(text)]


def backbone_steps_from_record(record: dict) -> list[str]:
    raw_steps = record.get('steps', []) if isinstance(record, dict) else []
    if isinstance(raw_steps, list):
        normalized = [_clean_step_text(item) for item in raw_steps if _clean_step_text(item)]
        if normalized:
            return normalized
    description = record.get('description', '') if isinstance(record, dict) else ''
    return parse_process_description_steps(str(description or ''))


def build_process_map_from_backbone(backbone_steps: list[str]) -> dict:
    main_steps = []
    for index, text in enumerate(backbone_steps, start=1):
        main_steps.append({
            'id': f'S{index}',
            'text': _clean_step_text(text),
            'lane': 'main',
            'team': 'Current Team',
            'parallel_of': None,
        })

    if not main_steps:
        return build_process_map_heuristic([])

    return {
        'summary': [
            'Backbone map started from your full process description.',
            'During discussion, micro-steps are added below each main step as needed.',
        ],
        'steps': main_steps,
    }


def should_refresh_process_map_from_chat(record: dict, messages: list, process_map_updated_at: str, choice: str, request_method: str) -> bool:
    if not isinstance(record, dict) or not isinstance(messages, list) or not messages:
        return False
    if str(request_method or '').strip().upper() != 'GET':
        return False
    if choice in ('restart', 'undo'):
        return False
    if record.get('discussion_mode') != 'deep' and not record.get('deep_dive_complete', False):
        return False
    return not str(process_map_updated_at or '').strip()


def extract_first_map_step_label_from_mermaid(mermaid_text: str) -> str:
    text = str(mermaid_text or '')
    if not text:
        return ''
    match = re.search(r'\bN1\["([^"]+)"\]', text)
    if not match:
        return ''
    return _clean_step_text(match.group(1))


def resolve_first_step_label_for_intro(record: dict, process_map: dict | None = None, process_map_mermaid: str = '') -> str:
    mermaid_text = str(process_map_mermaid or '')
    if not mermaid_text and isinstance(record, dict):
        mermaid_text = str(record.get('process_map_mermaid', '') or '')

    label_from_mermaid = extract_first_map_step_label_from_mermaid(mermaid_text)
    if label_from_mermaid:
        return label_from_mermaid

    map_dict = process_map if isinstance(process_map, dict) else {}
    if not map_dict and isinstance(record, dict):
        map_dict = record.get('process_map', {}) if isinstance(record.get('process_map', {}), dict) else {}

    step_items = map_dict.get('steps', []) if isinstance(map_dict, dict) else []
    if isinstance(step_items, list) and step_items:
        first_map_step = _clean_step_text(step_items[0].get('text', '')) if isinstance(step_items[0], dict) else ''
        if first_map_step:
            return first_map_step

    backbone_steps = backbone_steps_from_record(record)
    return backbone_steps[0] if backbone_steps else 'the first step you listed'


def deep_dive_intro_message(record: dict, first_step_label: str | None = None) -> str:
    first_step = _clean_step_text(first_step_label or '') or resolve_first_step_label_for_intro(record)
    return (
        f"Let's start with Step 1: \"{first_step}\". "
        'Please describe it in a little more detail: who is involved, how long it usually takes, '
        'what triggers it, which tools are used, and what output it creates.'
    )


def normalize_legacy_deep_intro(record: dict, messages: list, process_map: dict | None = None, process_map_mermaid: str = '') -> tuple[list, bool]:
    if not isinstance(messages, list) or not messages:
        return messages, False

    first = messages[0]
    if not isinstance(first, dict):
        return messages, False
    if str(first.get('role', '')).strip().lower() != 'assistant':
        return messages, False

    content = str(first.get('content', '') or '').strip()
    if not content:
        return messages, False

    legacy_markers = (
        'before we begin, here is a quick recap',
        'i will ask a few focused questions to better understand the workflow',
        'are you okay to proceed?',
        'let us start with step 1',
        "let's start with step 1",
    )
    lowered = content.lower()
    if not any(marker in lowered for marker in legacy_markers):
        return messages, False

    updated = list(messages)
    updated_first = dict(first)
    updated_content = deep_dive_intro_message(
        record,
        first_step_label=resolve_first_step_label_for_intro(
            record,
            process_map=process_map,
            process_map_mermaid=process_map_mermaid,
        ),
    )
    if updated_content == content:
        return messages, False
    updated_first['content'] = updated_content
    updated[0] = updated_first
    return updated, True


def enforce_backbone_on_map(candidate_map: dict, backbone_steps: list[str], fallback_messages: list) -> dict:
    if not backbone_steps:
        return sanitize_process_map(candidate_map, fallback_messages)

    sanitized = sanitize_process_map(candidate_map, fallback_messages)
    base = build_process_map_from_backbone(backbone_steps)
    main_ids = [f'S{i}' for i in range(1, len(backbone_steps) + 1)]
    main_id_set = set(main_ids)

    child_counts: dict[str, int] = {main_id: 0 for main_id in main_ids}
    steps = list(base['steps'])
    active_parent = main_ids[0]
    improvements: list[dict] = []
    improvement_idx = 0

    for item in sanitized.get('steps', []):
        if not isinstance(item, dict):
            continue
        lane = str(item.get('lane', 'main')).strip().lower()
        step_text = _clean_step_text(item.get('text', ''))
        if not step_text:
            continue

        if lane == 'improve':
            improvement_idx += 1
            improvements.append({
                'id': f'I{improvement_idx}',
                'text': _clean_step_text(step_text),
                'lane': 'improve',
                'team': _clean_step_text(item.get('team', '')) or 'Current Team',
                'parallel_of': None,
            })
            continue

        if lane == 'main':
            item_id = _clean_step_text(item.get('id', ''))
            if item_id in main_id_set:
                active_parent = item_id
            continue

        parent = _clean_step_text(item.get('parallel_of', ''))
        if parent not in main_id_set:
            id_hint = _clean_step_text(item.get('id', ''))
            hint_match = re.search(r'S\s*(\d+)', f'{id_hint} {step_text}', flags=re.IGNORECASE)
            if hint_match:
                hinted_parent = f"S{int(hint_match.group(1))}"
                if hinted_parent in main_id_set:
                    parent = hinted_parent
        if parent not in main_id_set:
            parent = active_parent

        child_counts[parent] += 1
        steps.append({
            'id': f"{parent}.{child_counts[parent]}",
            'text': _clean_step_text(step_text),
            'lane': 'below',
            'team': _clean_step_text(item.get('team', '')) or 'Current Team',
            'parallel_of': parent,
        })

    return {
        'summary': sanitized.get('summary', []) or base.get('summary', []),
        'steps': steps + improvements,
    }


def build_process_map_heuristic(messages: list) -> dict:
    user_texts = [str(m.get('content', '')).strip() for m in messages if m.get('role') == 'user']
    steps = []
    max_main_steps = 14
    main_index = 0

    for text in user_texts:
        if main_index >= max_main_steps:
            break

        parts = [
            _clean_step_text(part)
            for part in re.split(r'[\n\.;]+', text)
            if _clean_step_text(part)
        ]
        if not parts:
            continue

        main_text = parts[0]
        if len(main_text) < 8:
            continue

        main_index += 1
        main_id = f'S{main_index}'
        steps.append({
            'id': main_id,
            'text': _clean_step_text(main_text),
            'lane': 'main',
            'team': 'Current Team',
            'parallel_of': None,
        })

        substeps: list[str] = []
        marker_match = re.search(r'(?:sub[- ]?steps?|mini[- ]?steps?)\s*[:\-]\s*(.+)$', text, flags=re.IGNORECASE)
        if marker_match:
            marker_blob = marker_match.group(1)
            substeps.extend([
                _clean_step_text(item)
                for item in re.split(r',|;|\band\b|\bthen\b', marker_blob, flags=re.IGNORECASE)
            ])

        for extra in parts[1:4]:
            if len(extra) >= 6:
                substeps.append(extra)

        unique_substeps = []
        seen = set()
        for item in substeps:
            if len(item) < 6:
                continue
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            unique_substeps.append(item)

        for idx, sub in enumerate(unique_substeps[:3], start=1):
            steps.append({
                'id': f'{main_id}.{idx}',
                'text': _clean_step_text(sub),
                'lane': 'below',
                'team': 'Current Team',
                'parallel_of': main_id,
            })

    if not steps:
        steps = [{'id': 'S1', 'text': 'Start process mapping', 'lane': 'main', 'team': 'Current Team', 'parallel_of': None}]

    return {
        'summary': [
            'This is an initial draft map.',
            'Answer the next question to refine sequence, parallel steps, and handoffs.',
        ],
        'steps': steps,
    }


def sanitize_process_map(candidate: dict | None, fallback_messages: list) -> dict:
    if not isinstance(candidate, dict):
        return build_process_map_heuristic(fallback_messages)

    raw_steps = candidate.get('steps', [])
    sanitized_steps = []
    if isinstance(raw_steps, list):
        for item in raw_steps[:30]:
            if not isinstance(item, dict):
                continue
            step_id = _clean_step_text(item.get('id', '')) or f"S{len(sanitized_steps) + 1}"
            text = _clean_step_text(item.get('text', ''))
            if not text:
                continue
            lane = str(item.get('lane', 'main')).strip().lower()
            if lane not in ('main', 'above', 'below', 'improve'):
                lane = 'main'
            parallel_of = _clean_step_text(item.get('parallel_of', '')) or None
            if lane == 'improve':
                parallel_of = None
            team = _clean_step_text(item.get('team', '')) or 'Current Team'
            sanitized_steps.append({
                'id': step_id,
                'text': _clean_step_text(text),
                'lane': lane,
                'team': team,
                'parallel_of': parallel_of,
            })

    if not sanitized_steps:
        return build_process_map_heuristic(fallback_messages)

    raw_summary = candidate.get('summary', [])
    summary = []
    if isinstance(raw_summary, list):
        for line in raw_summary[:8]:
            text = _clean_step_text(line)
            if text:
                summary.append(text)

    return {
        'summary': summary,
        'steps': sanitized_steps,
    }


def extract_process_map_with_llm(record: dict, messages: list) -> dict:
    backbone_steps = backbone_steps_from_record(record)
    llm_config = get_llm_runtime_config()
    if not llm_config:
        if ENFORCE_MISTRAL_LIVE:
            base_map = build_process_map_from_backbone(backbone_steps)
            base_map['summary'] = [
                'Process map update paused: Mistral is unavailable or not configured.',
                'Set MISTRAL_API_KEY and retry to rebuild the map from live responses.',
            ]
            return base_map
        return enforce_backbone_on_map(build_process_map_heuristic(messages), backbone_steps, messages)

    backbone_text = '\n'.join([f'- S{idx}: {step}' for idx, step in enumerate(backbone_steps, start=1)])
    if not backbone_text:
        backbone_text = '- S1: Start process mapping'

    prompt = (
        'You are a process mapping assistant. Convert the discussion into a concise process map JSON.\n'
        'Return ONLY valid JSON (no markdown).\n'
        'Schema:\n'
        '{"summary":["..."],"steps":[{"id":"S1","text":"...","lane":"main|above|below|improve","team":"...","parallel_of":null}]}\n'
        'Rules:\n'
        '- Keep steps atomic and action-oriented.\n'
        '- Preserve the existing backbone main steps and their order exactly as provided.\n'
        '- Keep backbone main steps on lane="main".\n'
        '- If a backbone step has mini-steps/sub-steps, include those child steps with lane="below" and parallel_of pointing to the parent step id.\n'
        '- Add improvement suggestions as lane="improve" items in clear business language, ordered from most immediate impact to next best action.\n'
        '- Improvement suggestions must be specific and easy to understand (what to change, where, and expected benefit).\n'
        '- Do not create new main-lane steps unless the user explicitly changed the backbone.\n'
        '- Keep up to 24 total items.\n'
    )

    convo = []
    for msg in messages[-20:]:
        role = msg.get('role')
        content = str(msg.get('content', '')).strip()
        if role in ('user', 'assistant') and content:
            convo.append(f"{role.upper()}: {content}")

    try:
        prompt_messages = [
            {'role': 'system', 'content': prompt},
            {
                'role': 'user',
                'content': (
                    f"Process: {record.get('name', '')}\n"
                    f"Purpose: {record.get('purpose', '')}\n"
                    f"Backbone Main Steps:\n{backbone_text}\n\n"
                    "Conversation:\n" + '\n'.join(convo)
                ),
            },
        ]

        payload = {
            'model': llm_config['model'],
            'messages': prompt_messages,
            'temperature': 0.2,
        }
        req = urllib.request.Request(
            llm_config['chat_endpoint'],
            data=json.dumps(payload).encode('utf-8'),
            headers={
                'Content-Type': 'application/json',
                'Authorization': f"Bearer {llm_config['api_key']}",
            },
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=20, context=outbound_ssl_context()) as resp:
            body = json.loads(resp.read().decode('utf-8'))
        raw = body.get('choices', [{}])[0].get('message', {}).get('content', '').strip()

        candidate = json.loads(raw)
        return enforce_backbone_on_map(candidate, backbone_steps, messages)
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError, TimeoutError, json.JSONDecodeError) as exc:
        error_msg = str(exc)
        app.logger.warning('External LLM process-map call failed (provider=%s, model=%s): %s', llm_config.get('provider'), llm_config.get('model'), error_msg)
        # Fall back to heuristic map building from conversation when LLM fails
        # This ensures the map still updates with conversation context even if Mistral is unavailable
        heuristic_map = enforce_backbone_on_map(build_process_map_heuristic(messages), backbone_steps, messages)
        if ENFORCE_MISTRAL_LIVE and '429' in error_msg:
            # For rate-limit errors, provide informative feedback but still return the heuristic map
            heuristic_map['summary'] = [
                'Process map updated using conversation analysis (Mistral temporarily unavailable due to rate limiting).',
            ]
        return heuristic_map


def build_mermaid_flow(process_map: dict) -> str:
    steps = process_map.get('steps', []) if isinstance(process_map, dict) else []
    if not steps:
        return 'flowchart LR\nA([Start])\n'

    lines = [
        'flowchart TB',
        'classDef main fill:#dfeceb,stroke:#4b7470,color:#243235;',
        'classDef above fill:#eaf2ff,stroke:#4a79c9,color:#1f2e4f;',
        'classDef below fill:#fff1d8,stroke:#bd8b43,color:#513b15;',
        'classDef improve fill:#E2C6ED,stroke:#70537C,color:#2f1f36;',
    ]

    node_ids = {}
    ordered = []
    label_inputs: list[tuple[str, str]] = []
    full_text_map = {}
    for i, step in enumerate(steps, start=1):
        node = f'N{i}'
        step_id = str(step.get('id', '')).strip() or node
        label_key = f'{step_id}__{i}'
        node_ids[str(step.get('id'))] = node
        ordered.append((node, step, label_key))
        full_text = str(step.get('text', '')).strip()
        label_inputs.append((label_key, full_text))
        full_text_map[node] = full_text

    summarized_labels = summarize_long_map_labels_with_mistral(label_inputs, max_chars=50)

    improve_label_index = 0
    for node, step, label_key in ordered:
        display_text = summarized_labels.get(label_key, str(step.get('text', '')).strip())
        lane = step.get('lane', 'main')
        if lane == 'improve':
            improve_label_index += 1
            text_str = str(display_text).strip()
            if not re.match(r'^\d+\)', text_str):
                display_text = f'{improve_label_index}) {text_str}'
        label = str(display_text).replace('"', "'")
        lines.append(f'{node}["{label}"]')
        klass = 'main' if lane == 'main' else ('above' if lane == 'above' else ('below' if lane == 'below' else 'improve'))
        lines.append(f'class {node} {klass};')

    main_nodes = [node for node, step, _ in ordered if step.get('lane', 'main') == 'main']
    improve_nodes = [node for node, step, _ in ordered if step.get('lane', 'main') == 'improve']

    # Group non-main steps under their nearest/explicit parent to keep micro-steps
    # directly beneath each main step and reduce horizontal clutter.
    children_by_main: dict[str, list[str]] = {node: [] for node in main_nodes}
    last_main = main_nodes[0] if main_nodes else None
    for node, step, _ in ordered:
        lane = step.get('lane', 'main')
        if lane == 'main':
            last_main = node
            continue
        if lane == 'improve':
            continue

        anchor = node_ids.get(str(step.get('parallel_of'))) if step.get('parallel_of') else last_main
        if anchor in children_by_main:
            children_by_main[anchor].append(node)

    # Render each main step and its children in a vertical subgraph so children
    # appear directly below the parent step. Define the child links inside the
    # subgraph to make the layout engine honor the vertical stacking.
    for main_node in main_nodes:
        lines.append(f'subgraph cluster_{main_node}[ ]')
        lines.append('direction TB')
        child_nodes = children_by_main.get(main_node, [])
        if child_nodes:
            lines.append(f'{main_node} -.-> {child_nodes[0]}')
            for i in range(1, len(child_nodes)):
                lines.append(f'{child_nodes[i-1]} -.-> {child_nodes[i]}')
        else:
            lines.append(main_node)
        lines.append('end')

    # Keep backbone sequence left-to-right across main steps.
    for i in range(1, len(main_nodes)):
        lines.append(f'{main_nodes[i-1]} --> {main_nodes[i]}')

    # Place ordered improvement suggestions along the very bottom in a single row.
    if improve_nodes:
        lines.append('subgraph cluster_improvements[Improvement Suggestions]')
        lines.append('direction LR')
        lines.append(improve_nodes[0])
        for i in range(1, len(improve_nodes)):
            lines.append(f'{improve_nodes[i-1]} --> {improve_nodes[i]}')
        lines.append('end')

        # Anchor improvements below the mapped workflow.
        anchor = main_nodes[-1] if main_nodes else None
        if anchor:
            lines.append(f'{anchor} -.-> {improve_nodes[0]}')

    mermaid_text = '\n'.join(lines) + '\n'
    full_text_json = json.dumps(full_text_map, ensure_ascii=True)
    return f'%% {{fullTextMap: {full_text_json}}}\n{mermaid_text}'


def _extract_json_object_from_text(raw: str) -> dict | None:
    text = str(raw or '').strip()
    if not text:
        return None

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (TypeError, ValueError):
        pass

    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        parsed = json.loads(text[start:end + 1])
        if isinstance(parsed, dict):
            return parsed
    except (TypeError, ValueError):
        return None
    return None


def _fallback_map_label(text: str, max_chars: int) -> str:
    cleaned = _clean_step_text(text)
    if not cleaned:
        return ''
    if len(cleaned) <= max_chars:
        return cleaned
    short = _simplify_step_label(cleaned, max_chars=max_chars)
    if len(short) <= max_chars:
        return short
    return short[:max_chars].rstrip()


def summarize_long_map_labels_with_mistral(label_inputs: list[tuple[str, str]], max_chars: int = 50) -> dict[str, str]:
    if not isinstance(label_inputs, list) or not label_inputs:
        return {}

    long_inputs: list[tuple[str, str]] = []
    for key, text in label_inputs:
        cleaned = _clean_step_text(text)
        if len(cleaned) > max_chars:
            long_inputs.append((str(key), cleaned))

    if not long_inputs:
        return {}

    fallback = {key: _fallback_map_label(text, max_chars) for key, text in long_inputs}
    mistral_config = get_mistral_runtime_config()
    if not mistral_config:
        if ENFORCE_MISTRAL_LIVE:
            app.logger.warning('Mistral label summarization skipped: MISTRAL_API_KEY is missing.')
            return {}
        return fallback

    system_prompt = (
        'You shorten process step labels for a diagram. '
        'Return concise, specific action labels that preserve meaning. '
        f'Each label must be at most {max_chars} characters. '
        'Return JSON only in this exact format: '
        '{"labels":[{"key":"...","label":"..."}]}'
    )
    payload_items = [{'key': key, 'text': text} for key, text in long_inputs]

    try:
        payload = {
            'model': mistral_config['model'],
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': json.dumps({'items': payload_items}, ensure_ascii=True)},
            ],
            'temperature': 0.1,
        }
        req = urllib.request.Request(
            mistral_config['chat_endpoint'],
            data=json.dumps(payload).encode('utf-8'),
            headers={
                'Content-Type': 'application/json',
                'Authorization': f"Bearer {mistral_config['api_key']}",
            },
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=20, context=outbound_ssl_context()) as resp:
            body = json.loads(resp.read().decode('utf-8'))
        raw = body.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
        parsed = _extract_json_object_from_text(raw)
        if not parsed:
            return fallback

        labels = parsed.get('labels', [])
        if not isinstance(labels, list):
            return fallback

        result = dict(fallback)
        for item in labels:
            if not isinstance(item, dict):
                continue
            key = str(item.get('key', '')).strip()
            label = _clean_step_text(item.get('label', ''))
            if not key or key not in result or not label:
                continue
            if len(label) > max_chars:
                label = _fallback_map_label(label, max_chars)
            result[key] = label
        return result
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError, TimeoutError, json.JSONDecodeError) as exc:
        app.logger.warning('Mistral label-summarization failed: %s', exc)
        if ENFORCE_MISTRAL_LIVE:
            return {}
        return fallback


def _wrap_pdf_line(text: str, max_chars: int = 95) -> list[str]:
    words = str(text or '').split()
    if not words:
        return ['']

    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f'{current} {word}'
        if len(candidate) <= max_chars:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def build_process_map_pdf(record: dict, process_map: dict) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    steps = process_map.get('steps', []) if isinstance(process_map, dict) else []
    summary = process_map.get('summary', []) if isinstance(process_map, dict) else []

    buf = BytesIO()
    pdf = canvas.Canvas(buf, pagesize=letter)
    width, height = letter
    margin_x = 52
    y = height - 52
    line_height = 15

    def ensure_space() -> None:
        nonlocal y
        if y < 72:
            pdf.showPage()
            y = height - 52

    def write_line(text: str, font: str = 'Helvetica', size: int = 11) -> None:
        nonlocal y
        ensure_space()
        pdf.setFont(font, size)
        pdf.drawString(margin_x, y, str(text or ''))
        y -= line_height

    process_name = str(record.get('name', 'Process')).strip() or 'Process'
    write_line('Process Triage - Deep Dive Process Map', 'Helvetica-Bold', 15)
    y -= 4
    write_line(f'Process: {process_name}', 'Helvetica-Bold', 12)
    write_line(f'Generated: {utc_now().strftime("%Y-%m-%d %H:%M UTC")}', 'Helvetica', 10)
    y -= 6

    if summary:
        write_line('Map Summary', 'Helvetica-Bold', 12)
        for item in summary:
            for wrapped in _wrap_pdf_line(f'- {item}', 92):
                write_line(wrapped)
        y -= 6

    write_line('Process Steps', 'Helvetica-Bold', 12)
    if not steps:
        write_line('No process map steps were found for this submission.')
    else:
        for step in steps:
            step_id = str(step.get('id', '')).strip()
            lane = str(step.get('lane', 'main')).strip().lower()
            label_prefix = f'{step_id}: ' if step_id else ''
            step_text = str(step.get('text', '')).strip()
            if not step_text:
                continue

            if lane == 'below':
                parent = str(step.get('parallel_of', '')).strip()
                lead = f'    - Sub-step ({parent or "linked"}) {label_prefix}{step_text}'
            elif lane == 'above':
                parent = str(step.get('parallel_of', '')).strip()
                lead = f'  * Cross-team ({parent or "linked"}) {label_prefix}{step_text}'
            else:
                lead = f'- Main {label_prefix}{step_text}'

            for wrapped in _wrap_pdf_line(lead, 92):
                write_line(wrapped)

    pdf.save()
    return buf.getvalue()

@app.route('/')
def home():
    # Show the triage start page to everyone.
    # Guests can run Quick Look; signed-in users are directed to Deep Evaluation for new triages.
    user = current_user()
    return render_template(
        'landing.html',
        user_name=user.get('name', ''),
        user_first_name=first_name_from_user(user),
        user_email=user.get('email', ''),
        is_guest=('user' not in session),
    )


@app.route('/about')
def about():
    return render_template('about.html')


@app.route('/how-to')
def how_to():
    return render_template('how_to.html')

@app.route('/user', methods=['GET', 'POST'])
def user_info():
    # custom login/signup with persistent account storage
    if request.method == 'POST':
        try:
            mode = request.form.get('auth_mode', 'signup').strip().lower()
            email = normalize_email(request.form.get('user_email', ''))
            password = request.form.get('user_password', '')
            first = request.form.get('user_first_name', '').strip()
            last = request.form.get('user_last_name', '').strip()

            if not is_valid_email(email) or not password:
                error = 'Please provide a valid email and password.'
                return render_template('user.html', error=error, email=email, first_name=first, last_name=last, mode=mode)

            if mode == 'login':
                user = find_user_by_email(email)
                if not user or not password_matches(user.get('password_hash', ''), password):
                    error = 'Email or password is incorrect.'
                    return render_template('user.html', error=error, email=email, first_name='', last_name='', mode='login')
                session.permanent = False
                login_session_from_user(user)
                session['is_admin'] = is_admin_email(user.get('email', ''))
                if persist_guest_result_for_user(current_user()):
                    flash('Your guest triage was saved to your account.')
                return redirect(url_for('dashboard'))

            password2 = request.form.get('user_password_confirm', '')
            if not first or not last:
                error = 'Please provide both first and last name.'
                return render_template('user.html', error=error, email=email, first_name=first, last_name=last, mode='signup')
            if password != password2:
                error = 'Passwords do not match.'
                return render_template('user.html', error=error, email=email, first_name=first, last_name=last, mode='signup')
            pwd_error = password_strength_error(password)
            if pwd_error:
                return render_template('user.html', error=pwd_error, email=email, first_name=first, last_name=last, mode='signup')

            created, msg = create_user_account(first, last, email, password)
            if not created:
                return render_template('user.html', error=msg, email=email, first_name=first, last_name=last, mode='signup')

            user = find_user_by_email(email)
            session.permanent = False
            if user:
                login_session_from_user(user)
                session['is_admin'] = is_admin_email(user.get('email', ''))
                if persist_guest_result_for_user(current_user()):
                    flash('Your guest triage was saved to your account.')
            return redirect(url_for('dashboard'))
        except Exception:
            app.logger.exception('Unhandled exception during user login/signup flow')
            error = 'An unexpected error occurred while signing in. Please try again.'
            return render_template('user.html', error=error, email=request.form.get('user_email', ''), first_name=request.form.get('user_first_name', ''), last_name=request.form.get('user_last_name', ''), mode=request.form.get('auth_mode', 'signup'))
    return render_template('user.html', first_name='', last_name='', email='', mode='signup')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('user_info'))


@app.route('/feedback', methods=['GET', 'POST'])
def feedback_form():
    if 'user' not in session:
        return redirect(url_for('user_info'))

    user = current_user()
    allowed_types = {'request', 'idea', 'info'}
    selected_type = 'idea'
    message = ''
    error = None
    success = None

    if request.method == 'POST':
        selected_type = str(request.form.get('feedback_type', 'idea')).strip().lower()
        message = str(request.form.get('message', '')).strip()

        if selected_type not in allowed_types:
            error = 'Please choose a valid feedback type.'
        elif len(message) < 5:
            error = 'Please enter at least 5 characters.'
        elif len(message) > 4000:
            error = 'Please limit feedback to 4000 characters.'
        else:
            create_feedback_entry(user, selected_type, message)
            success = 'Thanks. Your feedback was submitted for admin review.'
            selected_type = 'idea'
            message = ''

    return render_template(
        'feedback.html',
        user_name=user.get('name', ''),
        user_email=user.get('email', ''),
        feedback_type=selected_type,
        message=message,
        error=error,
        success=success,
    )


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    seeded_admin_email = ensure_bootstrap_admin_account()
    error = None
    success = request.args.get('success', '').strip()
    default_email, _ = bootstrap_admin_credentials()
    email = default_email

    if not seeded_admin_email and not default_email and not os.environ.get('ADMIN_EMAILS', '').strip():
        error = 'Admin access is not configured. Set ADMIN_EMAILS or ADMIN_LOGIN_EMAIL and ADMIN_LOGIN_PASSWORD in environment variables.'

    if request.method == 'POST':
        try:
            email = normalize_email(request.form.get('email', ''))
            password = request.form.get('password', '')
            user = find_user_by_email(email)

            if not user or not password_matches(user.get('password_hash', ''), password):
                error = 'Admin email or password is incorrect.'
            elif not is_admin_email(email):
                error = 'This account is not allowed to access the admin area.'
            else:
                session.permanent = False
                login_session_from_user(user)
                session['is_admin'] = True
                return redirect(url_for('admin_db_view'))
        except Exception:
            app.logger.exception('Unhandled exception during admin login flow')
            error = 'An unexpected error occurred while signing in as admin. Please try again.'

    return render_template(
        'admin_login.html',
        error=error,
        success=success,
        email=email,
    )


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    error = None
    success = None
    info = None
    show_verify = False
    email = ''

    if request.method == 'POST':
        step = request.form.get('step', 'request').strip().lower()

        if step == 'request':
            email = normalize_email(request.form.get('email', ''))
            if not is_valid_email(email):
                error = 'Please enter a valid email address.'
            else:
                user = find_user_by_email(email)
                if user:
                    code = generate_reset_code()
                    session['password_reset'] = {
                        'email': email,
                        'code': code,
                        'expires_at': (utc_now() + timedelta(minutes=15)).isoformat(),
                    }
                    sent = send_password_reset_code(email, code)
                    show_verify = True
                    info = 'A verification code was sent to your email on file.'
                    if not sent:
                        info = 'Email delivery is not configured. Use the test code shown below.'
                else:
                    # Keep response generic to avoid exposing whether an email is registered.
                    info = 'If an account exists for that email, a verification code has been sent.'
                    show_verify = True

        elif step == 'verify':
            email = normalize_email(request.form.get('email', ''))
            code = request.form.get('code', '').strip()
            new_password = request.form.get('new_password', '')
            confirm = request.form.get('new_password_confirm', '')
            data = session.get('password_reset', {})
            show_verify = True

            if not is_valid_email(email):
                error = 'Please enter a valid email address.'
            elif not code:
                error = 'Please enter the verification code.'
            elif not new_password:
                error = 'Please enter a new password.'
            elif new_password != confirm:
                error = 'Passwords do not match.'
            elif password_strength_error(new_password):
                error = password_strength_error(new_password)
            elif not data or data.get('email') != email:
                error = 'Verification session not found. Please request a new code.'
            else:
                expires_at = data.get('expires_at', '')
                expires_on = parse_utc_datetime(expires_at)
                if not expires_on:
                    expired = True
                else:
                    expired = utc_now() > expires_on

                if expired:
                    error = 'Verification code has expired. Please request a new one.'
                elif data.get('code') != code:
                    error = 'Verification code is incorrect.'
                else:
                    if update_user_password(email, new_password):
                        session.pop('password_reset', None)
                        success = 'Password reset successful. You can now sign in.'
                        show_verify = False
                    else:
                        error = 'Could not reset password for this account.'

    data = session.get('password_reset', {})
    if data and not success:
        show_verify = True
        email = email or data.get('email', '')

    test_code = None
    if os.environ.get('SHOW_RESET_CODE', '1') == '1':
        if session.get('password_reset', {}).get('email') == email:
            test_code = session.get('password_reset', {}).get('code')

    return render_template(
        'forgot_password.html',
        error=error,
        success=success,
        info=info,
        show_verify=show_verify,
        email=email,
        test_code=test_code,
    )

@app.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    if 'user' not in session:
        return redirect(url_for('user_info'))
    user = current_user()
    error = None
    success = None
    if request.method == 'POST':
        current = request.form.get('current_password', '')
        new = request.form.get('new_password', '')
        new2 = request.form.get('new_password_confirm', '')
        stored = find_user_by_email(user.get('email', ''))
        if not stored or not check_password_hash(stored.get('password_hash', ''), current):
            error = 'Current password is incorrect.'
        elif not new:
            error = 'Please enter a new password.'
        elif new != new2:
            error = 'New passwords do not match.'
        elif password_strength_error(new):
            error = password_strength_error(new)
        else:
            if update_user_password(user.get('email', ''), new):
                success = 'Password updated.'
            else:
                error = 'Could not update password. Please try again.'
    return render_template('reset_password.html', error=error, success=success)


def is_admin_user(user: dict) -> bool:
    if session.get('is_admin', False):
        return True
    return is_admin_email(user.get('email', ''))


def require_admin():
    if 'user' not in session:
        return None, redirect(url_for('admin_login', success='Please sign in as admin.'))
    user = current_user()
    if not is_admin_user(user):
        return None, redirect(url_for('admin_login', success='Admin account required.'))
    return user, None


def admin_limit_from_request() -> int:
    limit = request.args.get('limit', '100').strip()
    try:
        return int(limit)
    except ValueError:
        return 100


def admin_deleted_filter_from_request() -> str:
    raw = request.args.get('deleted_filter', 'all').strip().lower()
    if raw in ('all', 'due_30', 'due_7'):
        return raw
    return 'all'


def admin_deleted_sort_from_request() -> str:
    raw = request.args.get('deleted_sort', 'days_asc').strip().lower()
    if raw in ('days_asc', 'days_desc'):
        return raw
    return 'days_asc'


def admin_user_search_from_request() -> str:
    return request.args.get('user_search', '').strip()


def build_deleted_retention_queue(limit: int, filter_mode: str = 'all', sort_mode: str = 'days_asc') -> tuple[list[dict], dict]:
    purge_expired_user_deleted_records()
    now_date = utc_now().date()
    rows = []

    for rec in load_data():
        deleted_at = str(rec.get('deleted_at', '')).strip()
        deleted_by = str(rec.get('deleted_by', '')).strip().lower()
        if not deleted_at or deleted_by != 'user':
            continue

        deleted_dt = parse_utc_datetime(deleted_at)
        purge_on = retention_delete_date_from_timestamp(deleted_at)
        purge_dt = parse_utc_datetime(purge_on)
        if not purge_dt:
            continue
        days_remaining = (purge_dt.date() - now_date).days

        user_payload = rec.get('user', {})
        if not isinstance(user_payload, dict):
            user_payload = {}

        row = {
            'id': rec.get('id', ''),
            'name': rec.get('name', ''),
            'user_email': user_payload.get('email', rec.get('user_email', '')),
            'deleted_at': deleted_dt.date().isoformat() if deleted_dt else deleted_at,
            'hard_delete_after': purge_dt.date().isoformat(),
            'days_remaining': days_remaining,
        }
        rows.append(row)

    if sort_mode == 'days_desc':
        rows.sort(key=lambda r: (r.get('days_remaining', -999999), r.get('id', '')), reverse=True)
    else:
        rows.sort(key=lambda r: (r.get('days_remaining', 999999), r.get('id', '')))

    if filter_mode == 'due_7':
        filtered = [r for r in rows if r['days_remaining'] <= 7]
    elif filter_mode == 'due_30':
        filtered = [r for r in rows if r['days_remaining'] <= 30]
    else:
        filtered = rows

    stats = {
        'total': len(rows),
        'due_30': len([r for r in rows if r['days_remaining'] <= 30]),
        'due_7': len([r for r in rows if r['days_remaining'] <= 7]),
    }

    return filtered[:limit], stats


def render_admin_db_page(user: dict, merge_preview: dict | None = None, merge_source_email: str = '', merge_target_email: str = ''):
    limit_int = admin_limit_from_request()
    deleted_filter = admin_deleted_filter_from_request()
    deleted_sort = admin_deleted_sort_from_request()
    user_search = admin_user_search_from_request()
    snapshot = get_admin_snapshot(limit=limit_int, user_search=user_search)
    deleted_queue, deleted_queue_stats = build_deleted_retention_queue(limit_int, deleted_filter, deleted_sort)
    return render_template(
        'admin_db.html',
        user_name=user.get('name', ''),
        user_email=user.get('email', ''),
        snapshot=snapshot,
        limit=limit_int,
        deleted_filter=deleted_filter,
        deleted_sort=deleted_sort,
        user_search=user_search,
        deleted_queue=deleted_queue,
        deleted_queue_stats=deleted_queue_stats,
        merge_preview=merge_preview,
        merge_source_email=merge_source_email,
        merge_target_email=merge_target_email,
    )


@app.route('/admin/db')
def admin_db_view():
    user, redirect_response = require_admin()
    if redirect_response:
        return redirect_response

    return render_admin_db_page(user)


@app.route('/admin/users/<path:user_email>/edit', methods=['GET', 'POST'])
def admin_edit_user(user_email):
    user, redirect_response = require_admin()
    if redirect_response:
        return redirect_response

    target_email = urllib.parse.unquote(user_email).strip().lower()
    users = load_users()
    target = next((u for u in users if str(u.get('email', '')).strip().lower() == target_email), None)
    if not target:
        flash('User not found.')
        return redirect(url_for('admin_db_view'))

    if request.method == 'POST':
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        email = request.form.get('email', '').strip().lower()

        if not email or '@' not in email:
            return render_template(
                'admin_edit_user.html',
                admin_name=user.get('name', ''),
                admin_email=user.get('email', ''),
                target_user={
                    'first_name': first_name,
                    'last_name': last_name,
                    'name': full_name(first_name, last_name),
                    'email': email,
                    'created': target.get('created', ''),
                },
                error='Please provide a valid email.',
            )

        duplicate = next(
            (
                u for u in users
                if str(u.get('email', '')).strip().lower() == email
                and str(u.get('email', '')).strip().lower() != target_email
            ),
            None,
        )
        if duplicate:
            return render_template(
                'admin_edit_user.html',
                admin_name=user.get('name', ''),
                admin_email=user.get('email', ''),
                target_user={
                    'first_name': first_name,
                    'last_name': last_name,
                    'name': full_name(first_name, last_name),
                    'email': email,
                    'created': target.get('created', ''),
                },
                error='Another account already uses that email.',
            )

        target['first_name'] = first_name
        target['last_name'] = last_name
        target['name'] = full_name(first_name, last_name)
        old_email = str(target.get('email', '')).strip().lower()
        target['email'] = email
        save_users(users)

        if email != old_email:
            records = load_data()
            for rec in records:
                rec_email = str(rec.get('user', {}).get('email', '')).strip().lower()
                if rec_email == old_email:
                    rec['user'] = {
                        **rec.get('user', {}),
                        'first_name': first_name,
                        'last_name': last_name,
                        'name': full_name(first_name, last_name),
                        'email': email,
                    }
            save_data(records)

        flash('User updated successfully.')
        return redirect(url_for('admin_db_view'))

    return render_template(
        'admin_edit_user.html',
        admin_name=user.get('name', ''),
        admin_email=user.get('email', ''),
        target_user=target,
        error=None,
    )


@app.route('/admin/users/<path:user_email>/delete', methods=['POST'])
def admin_delete_user(user_email):
    _, redirect_response = require_admin()
    if redirect_response:
        return redirect_response

    target_email = urllib.parse.unquote(user_email).strip().lower()
    users = load_users()
    kept_users = [u for u in users if str(u.get('email', '')).strip().lower() != target_email]
    if len(kept_users) == len(users):
        flash('User not found.')
        return redirect(url_for('admin_db_view'))

    save_users(kept_users)

    records = load_data()
    kept_records = [
        r for r in records
        if str(r.get('user', {}).get('email', '')).strip().lower() != target_email
    ]
    if len(kept_records) != len(records):
        save_data(kept_records)

    flash('User and associated submissions were permanently deleted by admin.')
    return redirect(url_for('admin_db_view'))


@app.route('/admin/assessments/<rec_id>/edit', methods=['GET', 'POST'])
def admin_edit_assessment(rec_id):
    user, redirect_response = require_admin()
    if redirect_response:
        return redirect_response

    records = load_data()
    rec = next((r for r in records if r.get('id') == rec_id), None)
    if not rec:
        flash('Submission not found.')
        return redirect(url_for('admin_db_view'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        purpose = request.form.get('purpose', '').strip()
        process_type = request.form.get('process_type', '').strip().upper()
        path = request.form.get('path', '').strip().lower()
        status = request.form.get('status', '').strip().lower()
        user_email = request.form.get('user_email', '').strip().lower()

        if process_type not in PROCESS_TEMPLATES:
            error = 'Process type must be one of: ' + ', '.join(PROCESS_TEMPLATES.keys())
            return render_template(
                'admin_edit_assessment.html',
                admin_name=user.get('name', ''),
                admin_email=user.get('email', ''),
                record={
                    **rec,
                    'name': name,
                    'purpose': purpose,
                    'type': process_type,
                    'path': path,
                    'status': status,
                    'user': {**rec.get('user', {}), 'email': user_email},
                },
                process_types=PROCESS_TEMPLATES,
                error=error,
            )

        if path not in ('quick', 'deep'):
            error = 'Path must be quick or deep.'
            return render_template(
                'admin_edit_assessment.html',
                admin_name=user.get('name', ''),
                admin_email=user.get('email', ''),
                record={
                    **rec,
                    'name': name,
                    'purpose': purpose,
                    'type': process_type,
                    'path': path,
                    'status': status,
                    'user': {**rec.get('user', {}), 'email': user_email},
                },
                process_types=PROCESS_TEMPLATES,
                error=error,
            )

        if status not in ('partial', 'submitted'):
            error = 'Status must be partial or submitted.'
            return render_template(
                'admin_edit_assessment.html',
                admin_name=user.get('name', ''),
                admin_email=user.get('email', ''),
                record={
                    **rec,
                    'name': name,
                    'purpose': purpose,
                    'type': process_type,
                    'path': path,
                    'status': status,
                    'user': {**rec.get('user', {}), 'email': user_email},
                },
                process_types=PROCESS_TEMPLATES,
                error=error,
            )

        if not user_email or '@' not in user_email:
            error = 'Please provide a valid user email for this submission.'
            return render_template(
                'admin_edit_assessment.html',
                admin_name=user.get('name', ''),
                admin_email=user.get('email', ''),
                record={
                    **rec,
                    'name': name,
                    'purpose': purpose,
                    'type': process_type,
                    'path': path,
                    'status': status,
                    'user': {**rec.get('user', {}), 'email': user_email},
                },
                process_types=PROCESS_TEMPLATES,
                error=error,
            )

        rec['name'] = name
        rec['purpose'] = purpose
        rec['type'] = process_type
        rec['path'] = path
        rec['status'] = status
        rec['updated'] = current_date_string()
        rec['user'] = {
            **rec.get('user', {}),
            'email': user_email,
        }
        save_data(records)
        flash('Submission updated successfully.')
        return redirect(url_for('admin_db_view'))

    return render_template(
        'admin_edit_assessment.html',
        admin_name=user.get('name', ''),
        admin_email=user.get('email', ''),
        record=rec,
        process_types=PROCESS_TEMPLATES,
        error=None,
    )


@app.route('/admin/assessments/<rec_id>/delete', methods=['POST'])
def admin_delete_assessment(rec_id):
    _, redirect_response = require_admin()
    if redirect_response:
        return redirect_response

    records = load_data()
    kept = [r for r in records if r.get('id') != rec_id]
    if len(kept) == len(records):
        flash('Submission not found.')
        return redirect(url_for('admin_db_view'))

    save_data(kept)
    flash('Submission permanently deleted by admin.')
    return redirect(url_for('admin_db_view'))


@app.route('/admin/users/merge', methods=['POST'])
def admin_merge_users():
    user, redirect_response = require_admin()
    if redirect_response:
        return redirect_response

    merge_action = request.form.get('merge_action', 'preview').strip().lower()
    source_email = request.form.get('source_email', '').strip().lower()
    target_email = request.form.get('target_email', '').strip().lower()

    if not source_email or not target_email or '@' not in source_email or '@' not in target_email:
        flash('Enter valid source and target emails.')
        return redirect(url_for('admin_db_view'))
    if source_email == target_email:
        flash('Source and target emails must be different.')
        return redirect(url_for('admin_db_view'))
    if source_email == str(user.get('email', '')).strip().lower():
        flash('For safety, you cannot merge from the account you are currently using.')
        return redirect(url_for('admin_db_view'))

    users = load_users()
    source_user = next((u for u in users if str(u.get('email', '')).strip().lower() == source_email), None)
    target_user = next((u for u in users if str(u.get('email', '')).strip().lower() == target_email), None)
    if not source_user or not target_user:
        flash('Both source and target accounts must exist to merge.')
        return redirect(url_for('admin_db_view'))

    records = load_data()
    records_to_move = [
        rec for rec in records
        if str(rec.get('user', {}).get('email', '')).strip().lower() == source_email
    ]

    if merge_action != 'confirm':
        return render_admin_db_page(
            user,
            merge_preview={
                'source_email': source_email,
                'target_email': target_email,
                'source_name': source_user.get('name', ''),
                'target_name': target_user.get('name', ''),
                'records_to_move': len(records_to_move),
            },
            merge_source_email=source_email,
            merge_target_email=target_email,
        )

    for rec in records:
        rec_email = str(rec.get('user', {}).get('email', '')).strip().lower()
        if rec_email == source_email:
            rec['user'] = {
                **rec.get('user', {}),
                'first_name': target_user.get('first_name', ''),
                'last_name': target_user.get('last_name', ''),
                'name': target_user.get('name', full_name(target_user.get('first_name', ''), target_user.get('last_name', ''))),
                'email': target_email,
            }
            rec['updated'] = current_date_string()
    save_data(records)

    kept_users = [u for u in users if str(u.get('email', '')).strip().lower() != source_email]
    save_users(kept_users)

    flash(f'Accounts merged. Moved {len(records_to_move)} submissions from {source_email} to {target_email}.')
    return redirect(url_for('admin_db_view'))

@app.route('/dashboard')
def dashboard():
    # welcome screen for current user
    if 'user' not in session:
        return redirect(url_for('user_info'))
    user = current_user()
    return render_template('dashboard.html',
                           user_first_name=first_name_from_user(user),
                           user_name=user.get('name', ''),
                           user_email=user.get('email', ''),
                           is_admin=is_admin_user(user))


@app.route('/record/<rec_id>/delete', methods=['POST'])
def delete_record(rec_id):
    if 'user' not in session:
        return redirect(url_for('user_info'))
    ok = delete_user_record(rec_id, current_user().get('email', ''))
    if ok:
        flash('Submission moved to Recently Deleted. It can be restored, and is permanently deleted after 2 years.')
    else:
        flash('Submission not found or already removed.')
    return redirect(url_for('items'))


@app.route('/record/<rec_id>/restore', methods=['POST'])
def restore_record(rec_id):
    if 'user' not in session:
        return redirect(url_for('user_info'))
    ok = restore_user_record(rec_id, current_user().get('email', ''))
    if ok:
        flash('Submission restored.')
    else:
        flash('Submission not found or cannot be restored.')
    return redirect(url_for('items'))


@app.route('/record/<rec_id>/discussion', methods=['GET', 'POST'])
def record_discussion(rec_id):
    if 'user' not in session:
        return redirect(url_for('user_info'))
    rec = find_record(rec_id)
    if not rec:
        return '<h1>Record not found</h1>', 404
    if rec.get('user', {}).get('email') != current_user().get('email'):
        return '<h1>Forbidden</h1>', 403

    messages = rec.get('llm_chat', []) if isinstance(rec.get('llm_chat'), list) else []
    mode = request.args.get('mode', '').strip().lower()
    choice = request.args.get('choice', '').strip().lower()
    deep_dive = mode == 'deep' or rec.get('discussion_mode') == 'deep'
    deep_dive_complete = bool(rec.get('deep_dive_complete', False))
    process_map = rec.get('process_map', {}) if isinstance(rec.get('process_map', {}), dict) else {}
    process_map_mermaid = str(rec.get('process_map_mermaid', '') or '')
    process_map_updated_at = str(rec.get('process_map_updated_at', '') or '')
    mistral_live_available = bool(get_mistral_runtime_config())
    show_mistral_unavailable_banner = ENFORCE_MISTRAL_LIVE and not mistral_live_available
    map_changed_this_request = False

    if deep_dive and messages and should_refresh_process_map_from_chat(rec, messages, process_map_updated_at, choice, request.method):
        process_map = extract_process_map_with_llm(rec, messages)
        process_map_mermaid = build_mermaid_flow(process_map)
        process_map_updated_at = utc_now().isoformat()
        rec['process_map'] = process_map
        rec['process_map_mermaid'] = process_map_mermaid
        rec['process_map_updated_at'] = process_map_updated_at
        upsert_partial_record(current_user(), rec.get('path', 'quick'), rec, rec_id)

    # Ensure an existing deep-dive thread has a rendered map before we normalize
    # the opening prompt, so Step 1 text matches the first visible map tile.
    if deep_dive and messages and not process_map_mermaid:
        process_map = enforce_backbone_on_map(rec.get('process_map', {}), backbone_steps_from_record(rec), messages)
        process_map_mermaid = build_mermaid_flow(process_map)
        rec['process_map'] = process_map
        rec['process_map_mermaid'] = process_map_mermaid

    if deep_dive and messages:
        normalized_messages, intro_changed = normalize_legacy_deep_intro(
            rec,
            messages,
            process_map=process_map,
            process_map_mermaid=process_map_mermaid,
        )
        if intro_changed:
            messages = normalized_messages
            rec['llm_chat'] = messages
            rec['llm_chat_updated'] = utc_now().isoformat()
            upsert_partial_record(current_user(), rec.get('path', 'quick'), rec, rec_id)

    if deep_dive and messages and not deep_dive_complete and deep_dive_has_recommendations(messages):
        deep_dive_complete = True
        rec['deep_dive_complete'] = True
        rec['llm_chat'] = messages
        rec['llm_chat_updated'] = utc_now().isoformat()
        upsert_partial_record(current_user(), rec.get('path', 'quick'), rec, rec_id)

    if deep_dive and messages and not deep_dive_complete and request.method == 'GET' and choice not in ('continue', 'restart', 'undo'):
        return render_template(
            'discussion.html',
            record=rec,
            messages=messages,
            has_chat=bool(messages),
            deep_dive=deep_dive,
            process_map=process_map,
            process_map_mermaid=process_map_mermaid,
            map_changed_this_request=map_changed_this_request,
            show_mistral_unavailable_banner=show_mistral_unavailable_banner,
            user_name=current_user().get('name', ''),
            user_email=current_user().get('email', ''),
        )

    if deep_dive and choice == 'undo':
        rec['discussion_mode'] = 'deep'
        messages = rewind_deep_dive_messages(messages)
        rec['llm_chat'] = messages
        rec['deep_dive_complete'] = False
        if messages:
            process_map = extract_process_map_with_llm(rec, messages)
            process_map_mermaid = build_mermaid_flow(process_map)
        else:
            process_map = {}
            process_map_mermaid = ''
        rec['process_map'] = process_map
        rec['process_map_mermaid'] = process_map_mermaid
        rec['llm_chat_updated'] = utc_now().isoformat()
        upsert_partial_record(current_user(), rec.get('path', 'quick'), rec, rec_id)
        return redirect(url_for('record_discussion', rec_id=rec_id, mode='deep', choice='continue'))

    if deep_dive and choice == 'restart':
        rec['discussion_mode'] = 'deep'
        rec['deep_dive_complete'] = False
        messages = []
        rec['llm_chat'] = []
        rec['process_map'] = {}
        rec['process_map_mermaid'] = ''
        rec['llm_chat_updated'] = utc_now().isoformat()
        upsert_partial_record(current_user(), rec.get('path', 'quick'), rec, rec_id)
        return redirect(url_for('record_discussion', rec_id=rec_id, mode='deep', choice='continue'))

    if not messages and deep_dive:
        rec['discussion_mode'] = 'deep'
        rec['deep_dive_complete'] = False
        backbone_steps = backbone_steps_from_record(rec)
        if backbone_steps:
            rec['steps'] = backbone_steps
        process_map = build_process_map_from_backbone(backbone_steps)
        process_map_mermaid = build_mermaid_flow(process_map)
        first_step_label = resolve_first_step_label_for_intro(rec, process_map=process_map, process_map_mermaid=process_map_mermaid)
        intro = deep_dive_intro_message(rec, first_step_label=first_step_label)
        messages = [{'role': 'assistant', 'content': intro, 'timestamp': utc_now().isoformat()}]
        rec['process_map'] = process_map
        rec['process_map_mermaid'] = process_map_mermaid

    if request.method == 'POST':
        cancel_action = request.form.get('cancel_action', '').strip()
        if deep_dive and cancel_action == 'save_and_close':
            rec['discussion_mode'] = 'deep'
            rec['llm_chat'] = messages
            rec['process_map'] = process_map
            rec['process_map_mermaid'] = process_map_mermaid
            rec['process_map_updated_at'] = process_map_updated_at or utc_now().isoformat()
            if deep_dive and messages and not deep_dive_complete and deep_dive_has_recommendations(messages):
                rec['deep_dive_complete'] = True
            rec['llm_chat_updated'] = utc_now().isoformat()
            upsert_partial_record(current_user(), rec.get('path', 'quick'), rec, rec_id)
            return redirect(url_for('items'))

        prompt = request.form.get('message', '').strip()
        if prompt:
            messages.append({'role': 'user', 'content': prompt, 'timestamp': utc_now().isoformat()})
            if deep_dive:
                rec['discussion_mode'] = 'deep'
            previous_process_map_mermaid = process_map_mermaid
            assistant_reply = llm_reply(rec, prompt, chat_history=messages, deep_dive=deep_dive)
            messages.append({
                'role': 'assistant',
                'content': assistant_reply,
                'timestamp': utc_now().isoformat(),
            })
            rec['llm_chat'] = messages
            if deep_dive:
                process_map = extract_process_map_with_llm(rec, messages)
                process_map_mermaid = build_mermaid_flow(process_map)
                rec['process_map'] = process_map
                rec['process_map_mermaid'] = process_map_mermaid
                rec['process_map_updated_at'] = utc_now().isoformat()
                map_changed_this_request = process_map_mermaid != previous_process_map_mermaid
            if deep_dive and deep_dive_has_recommendations(messages):
                rec['deep_dive_complete'] = True
            rec['llm_chat_updated'] = utc_now().isoformat()
            upsert_partial_record(current_user(), rec.get('path', 'quick'), rec, rec_id)

            if request.headers.get('X-Requested-With', '').strip().lower() == 'xmlhttprequest':
                summary_lines = process_map.get('summary', []) if isinstance(process_map, dict) else []
                if not isinstance(summary_lines, list):
                    summary_lines = []
                summary_lines = [str(line) for line in summary_lines]
                has_steps = bool(process_map.get('steps')) if isinstance(process_map, dict) else False
                return jsonify({
                    'ok': True,
                    'user_message': prompt,
                    'assistant_message': assistant_reply,
                    'deep_dive': bool(deep_dive),
                    'process_map_mermaid': str(process_map_mermaid or ''),
                    'process_map_summary': summary_lines,
                    'has_steps': has_steps,
                    'map_changed': bool(map_changed_this_request),
                })
        if deep_dive:
            return redirect(url_for('record_discussion', rec_id=rec_id, mode='deep', choice='continue'))
        return redirect(url_for('record_discussion', rec_id=rec_id))

    return render_template(
        'discussion.html',
        record=rec,
        messages=messages,
        has_chat=bool(messages),
        deep_dive=deep_dive,
        process_map=process_map,
        process_map_mermaid=process_map_mermaid,
        map_changed_this_request=map_changed_this_request,
        show_mistral_unavailable_banner=show_mistral_unavailable_banner,
        user_name=current_user().get('name', ''),
        user_email=current_user().get('email', ''),
    )

@app.route('/record/<rec_id>')
def view_record(rec_id):
    if 'user' not in session:
        return redirect(url_for('user_info'))
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


@app.route('/record/<rec_id>/map.pdf')
def download_process_map_pdf(rec_id):
    if 'user' not in session:
        return redirect(url_for('user_info'))

    rec = find_record(rec_id)
    if not rec:
        return '<h1>Record not found</h1>', 404

    user = current_user()
    if rec.get('user', {}).get('email') != user.get('email'):
        return '<h1>Forbidden</h1>', 403

    if rec.get('path') != 'deep':
        return '<h1>Process map export is available for deep-dive submissions only.</h1>', 400

    messages = rec.get('llm_chat', []) if isinstance(rec.get('llm_chat'), list) else []
    process_map = sanitize_process_map(rec.get('process_map'), messages)
    rec['process_map'] = process_map

    try:
        pdf_bytes = build_process_map_pdf(rec, process_map)
    except ImportError:
        return '<h1>PDF export dependency is missing. Install reportlab and try again.</h1>', 500

    safe_name = re.sub(r'[^A-Za-z0-9_-]+', '-', str(rec.get('name', 'process-map')).strip()).strip('-').lower()
    if not safe_name:
        safe_name = f'process-map-{rec_id.lower()}'
    filename = f'{safe_name}-map.pdf'

    return send_file(
        BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename,
    )

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
    # Logged-in users always start new triage in Deep Evaluation.
    if 'user' in session:
        return redirect(url_for('deep_evaluation'))

    # Guests can run Quick Look only.
    path = request.args.get('path')
    if path not in ('quick', 'deep'):
        return redirect(url_for('home'))
    if path == 'quick':
        return redirect(url_for('quick_start'))
    flash('Please sign in to access Deep Evaluation.')
    return redirect(url_for('user_info'))

@app.route('/quick', methods=['GET', 'POST'])
def quick_start():
    if 'user' in session:
        # Signed-in users should use Deep Evaluation for all new triages.
        return redirect(url_for('deep_evaluation'))

    # initial step: gather name/type/purpose
    error = None
    is_guest = True
    if request.method == 'POST':
        cancel_action = request.form.get('cancel_action', '').strip()
        if cancel_action in ('save_before_closing', 'close_without_saving'):
            if not is_guest and 'user' in session:
                email = session['user'].get('email', '')
                if cancel_action == 'save_before_closing':
                    name = request.form.get('process_name', '').strip()
                    proc_type = request.form.get('process_type')
                    purpose = request.form.get('purpose', '').strip()
                    draft_id = upsert_partial_record(
                        current_user(),
                        'quick',
                        {
                            'name': name,
                            'purpose': purpose,
                            'type': proc_type if proc_type in PROCESS_TEMPLATES else '',
                            'steps': [],
                            'answers': {},
                            'score': None,
                            'status': 'partial',
                        },
                        session.get('quick_draft_id'),
                    )
                    if draft_id:
                        session['quick_draft_id'] = draft_id
                else:
                    delete_user_record(session.get('quick_draft_id'), email)
            session.pop('quick_base', None)
            session.pop('quick_draft_id', None)
            session.pop('guest_mode', None)
            if 'user' in session:
                return redirect(url_for('dashboard'))
            return redirect(url_for('home'))

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
            if not is_guest and 'user' in session:
                draft_id = upsert_partial_record(
                    current_user(),
                    'quick',
                    {
                        'name': name,
                        'purpose': purpose,
                        'type': proc_type,
                        'steps': [],
                        'answers': {},
                        'score': None,
                        'status': 'partial',
                    },
                    session.get('quick_draft_id'),
                )
                if draft_id:
                    session['quick_draft_id'] = draft_id
            return redirect(url_for('quick_details'))
    base = session.get('quick_base', {})
    return render_template('quick_start.html', templates=PROCESS_TEMPLATES,
                           name=base.get('name',''), selected_type=base.get('type',''),
                           purpose=base.get('purpose',''), error=error,
                           user_name='', user_email='', is_guest=is_guest)


@app.route('/quick/cancel')
def quick_cancel():
    # fallback cancel route: close without saving
    if 'user' in session:
        delete_user_record(session.get('quick_draft_id'), session['user'].get('email', ''))
    session.pop('quick_base', None)
    session.pop('quick_draft_id', None)
    session.pop('guest_mode', None)
    if 'user' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('home'))

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
        values = rec.get('answers', {}) if isinstance(rec.get('answers', {}), dict) else {}
        return render_template('quick.html', templates=PROCESS_TEMPLATES,
                               user_name=session['user']['name'], user_email=session['user']['email'],
                               name=rec.get('name'), purpose=rec.get('purpose'),
                               selected_type=rec.get('type'), values=values)
    # POST update
    user_name = session['user'].get('name','')
    user_email = session['user'].get('email','')
    name = request.form.get('process_name', '').strip()
    purpose = request.form.get('purpose', rec.get('purpose', '')).strip()
    proc_type = request.form.get('process_type')
    missing=[]
    if not name:
        missing.append('process name')
    if proc_type not in PROCESS_TEMPLATES:
        missing.append('process type')
    if missing:
        error='Please provide '+', '.join(missing)+'.'
        return render_template('quick.html', error=error, templates=PROCESS_TEMPLATES,
                               name=name, purpose=purpose,
                               selected_type=proc_type, user_name=user_name, user_email=user_email)
    answers={}
    for q in PROCESS_TEMPLATES[proc_type]['questions']:
        key=q['key']
        if q.get('multiple'):
            answers[key] = request.form.getlist(key)
        else:
            answers[key] = request.form.get(key, '').strip()
    
    # Validate answers
    missing_answers = []
    for q in PROCESS_TEMPLATES[proc_type]['questions']:
        key = q['key']
        if not answers.get(key):
            missing_answers.append(q['text'])
    
    if missing_answers:
        error='Please answer all required questions.'
        return render_template('quick.html', error=error, templates=PROCESS_TEMPLATES,
                               name=name, purpose=purpose,
                               selected_type=proc_type, user_name=user_name, user_email=user_email, values=answers)
    # update record
    rec.update({
        'name': name,
        'purpose': purpose,
        'type': proc_type,
        'steps': [],
        'answers':answers
    })
    rec['score'] = score_answers(proc_type, answers)
    rec['status'] = 'submitted'
    upsert_partial_record(current_user(), 'quick', rec, rec_id)
    percent=rec['score']['percent']
    recmd=rec['score']['recommendation']
    return render_template('quick_result.html', record=rec, percent=percent, recommendation=recmd)


@app.route('/deep/edit/<rec_id>', methods=['GET', 'POST'])
def deep_edit(rec_id):
    # modify an existing deep record
    if 'user' not in session:
        return redirect(url_for('user_info'))
    rec = find_record(rec_id)
    if not rec:
        return '<h1>Record not found</h1>', 404
    if rec.get('user', {}).get('email') != session['user']['email']:
        return '<h1>Forbidden</h1>', 403

    if request.method == 'GET':
        values = rec.get('answers', {}) if isinstance(rec.get('answers', {}), dict) else {}
        return render_template(
            'deep.html',
            templates=PROCESS_TEMPLATES,
            user_name=session['user']['name'],
            user_email=session['user']['email'],
            name=rec.get('name', ''),
            purpose=rec.get('purpose', ''),
            description=rec.get('description', ''),
            selected_type=rec.get('type', ''),
            values=values,
        )

    user_name = session['user'].get('name', '')
    user_email = session['user'].get('email', '')
    name = request.form.get('process_name', '').strip()
    purpose = request.form.get('purpose', rec.get('purpose', '')).strip()
    description = request.form.get('description', '').strip()
    proc_type = request.form.get('process_type')
    answers = {}
    if proc_type in PROCESS_TEMPLATES:
        for q in PROCESS_TEMPLATES[proc_type]['questions']:
            key = q['key']
            if q.get('multiple'):
                answers[key] = request.form.getlist(key)
            else:
                answers[key] = request.form.get(key, '').strip()

    missing = []
    if not name:
        missing.append('process name')
    if not description:
        missing.append('description')
    if proc_type not in PROCESS_TEMPLATES:
        missing.append('process type')

    if missing:
        error = 'Please provide ' + ', '.join(missing) + '.'
        return render_template(
            'deep.html',
            error=error,
            templates=PROCESS_TEMPLATES,
            user_name=user_name,
            user_email=user_email,
            name=name,
            description=description,
            selected_type=proc_type,
            values=answers,
        )

    missing_answers = []
    for q in PROCESS_TEMPLATES[proc_type]['questions']:
        key = q['key']
        if not answers.get(key):
            missing_answers.append(q['text'])

    if missing_answers:
        error = 'Please answer all required questions.'
        return render_template(
            'deep.html',
            error=error,
            templates=PROCESS_TEMPLATES,
            user_name=user_name,
            user_email=user_email,
            name=name,
            description=description,
            selected_type=proc_type,
            values=answers,
        )

    score_info = score_answers(proc_type, answers)
    record = {
        'id': rec_id,
        'created': rec.get('created', current_date_string()),
        'updated': current_date_string(),
        'path': 'deep',
        'user': {'name': user_name, 'email': user_email},
        'name': name,
        'purpose': purpose,
        'type': proc_type,
        'steps': parse_process_description_steps(description),
        'description': description,
        'answers': answers,
        'score': score_info,
        'status': 'submitted',
    }

    upsert_partial_record(current_user(), 'deep', record, rec_id)
    percent = score_info['percent']
    recommendation = score_info['recommendation']
    return render_template('deep_result.html', record=record, percent=percent, recommendation=recommendation)

@app.route('/quick/details', methods=['GET', 'POST'])
def quick_details():
    if 'user' in session:
        # Signed-in users should use Deep Evaluation for all new triages.
        return redirect(url_for('deep_evaluation'))

    # second step: step+questions
    base = session.get('quick_base')
    if not base:
        return redirect(url_for('quick_start'))
    is_guest = True
    user_name = session['user'].get('name','') if 'user' in session else ''
    user_email = session['user'].get('email','') if 'user' in session else ''
    if request.method == 'POST':
        proc_type = base.get('type')
        cancel_action = request.form.get('cancel_action', '').strip()

        if cancel_action in ('save_before_closing', 'close_without_saving'):
            if not is_guest and 'user' in session:
                if cancel_action == 'save_before_closing':
                    answers = {}
                    if proc_type in PROCESS_TEMPLATES:
                        for q in PROCESS_TEMPLATES[proc_type]['questions']:
                            key = q['key']
                            if q.get('multiple'):
                                answers[key] = request.form.getlist(key)
                            else:
                                answers[key] = request.form.get(key, '').strip()
                    draft_id = upsert_partial_record(
                        current_user(),
                        'quick',
                        {
                            'name': base.get('name', ''),
                            'purpose': base.get('purpose', ''),
                            'type': proc_type if proc_type in PROCESS_TEMPLATES else '',
                            'steps': [],
                            'answers': answers,
                            'score': None,
                            'status': 'partial',
                        },
                        session.get('quick_draft_id'),
                    )
                    if draft_id:
                        session['quick_draft_id'] = draft_id
                else:
                    delete_user_record(session.get('quick_draft_id'), user_email)
            session.pop('quick_base', None)
            session.pop('quick_draft_id', None)
            session.pop('guest_mode', None)
            if 'user' in session:
                return redirect(url_for('dashboard'))
            return redirect(url_for('home'))

        missing = []
        if proc_type not in PROCESS_TEMPLATES:
            missing.append('process type')
        if missing:
            error = 'Please provide ' + ', '.join(missing) + '.'
            return render_template('quick.html', error=error, templates=PROCESS_TEMPLATES,
                                   name=base.get('name'), purpose=base.get('purpose'),
                                   selected_type=proc_type,
                                   user_name=user_name, user_email=user_email)
        answers = {}
        for q in PROCESS_TEMPLATES[proc_type]['questions']:
            key = q['key']
            if q.get('multiple'):
                # Multi-select question: collect all checked values
                answers[key] = request.form.getlist(key)
            else:
                # Single-select question: get the selected value
                answers[key] = request.form.get(key, '').strip()
        
        # Validate that required questions are answered
        missing_answers = []
        for q in PROCESS_TEMPLATES[proc_type]['questions']:
            key = q['key']
            if q.get('multiple'):
                if not answers.get(key):
                    missing_answers.append(q['text'])
            else:
                if not answers.get(key):
                    missing_answers.append(q['text'])
        
        if missing_answers:
            error = 'Please answer all required questions.'
            return render_template('quick.html', error=error, templates=PROCESS_TEMPLATES,
                                   name=base.get('name'), purpose=base.get('purpose'),
                                   selected_type=proc_type,
                                   user_name=user_name, user_email=user_email, values=answers)
        
        score_info = score_answers(proc_type, answers)
        percent = score_info['percent']
        rec = score_info['recommendation']
        record = {
            'id': session.get('quick_draft_id') or next_submission_id(),
            'created': current_date_string(),
            'updated': current_date_string(),
            'path': 'quick',
            'user': {'name': user_name, 'email': user_email},
            'name': base.get('name'),
            'purpose': base.get('purpose'),
            'type': proc_type,
            # analytics processes may not have steps
            'steps': [],
            'answers': answers,
            'score': score_info,
            'status': 'submitted',
        }
        # if not guest, persist; guests see results but nothing is saved yet
        if not is_guest:
            draft_id = session.get('quick_draft_id')
            upsert_partial_record(current_user(), 'quick', record, draft_id)
        else:
            # store guest result in session temporarily so they can save it after login
            session['guest_result'] = record
        session.pop('quick_base', None)
        session.pop('quick_draft_id', None)
        if is_guest:
            session.pop('guest_mode', None)
        return render_template('quick_result.html', record=record, percent=percent, recommendation=rec, is_guest=is_guest)
    return render_template('quick.html', templates=PROCESS_TEMPLATES,
                           user_name=user_name, user_email=user_email,
                           name=base.get('name'), purpose=base.get('purpose'),
                           selected_type=base.get('type'), is_guest=is_guest)

@app.route('/deep', methods=['GET', 'POST'])
def deep_evaluation():
    if 'user' not in session:
        return redirect(url_for('user_info'))

    # deeper form with additional descriptive fields; allow guest mode
    # deeper form with additional descriptive fields
    if request.method == 'POST':
        # determine user from session or form
        is_guest = False
        if 'user' in session:
            user_name = session['user'].get('name','')
            user_email = session['user'].get('email','')
        else:
            user_name = request.form.get('user_name', '').strip()
            user_email = request.form.get('user_email', '').strip()
        name = request.form.get('process_name', '').strip()
        purpose = request.form.get('purpose', '').strip()
        description = request.form.get('description', '').strip()
        proc_type = request.form.get('process_type')
        answers = {}
        if proc_type in PROCESS_TEMPLATES:
            for q in PROCESS_TEMPLATES[proc_type]['questions']:
                key = q['key']
                if q.get('multiple'):
                    answers[key] = request.form.getlist(key)
                else:
                    answers[key] = request.form.get(key, '').strip()
        cancel_action = request.form.get('cancel_action', '').strip()

        if cancel_action in ('save_before_closing', 'close_without_saving'):
            if not is_guest and 'user' in session:
                if cancel_action == 'save_before_closing':
                    answers = {}
                    if proc_type in PROCESS_TEMPLATES:
                        for q in PROCESS_TEMPLATES[proc_type]['questions']:
                            key = q['key']
                            if q.get('multiple'):
                                answers[key] = request.form.getlist(key)
                            else:
                                answers[key] = request.form.get(key, '').strip()
                    draft_id = upsert_partial_record(
                        current_user(),
                        'deep',
                        {
                            'name': name,
                            'purpose': purpose,
                            'type': proc_type if proc_type in PROCESS_TEMPLATES else '',
                            'steps': parse_process_description_steps(description),
                            'description': description,
                            'answers': answers,
                            'score': None,
                            'status': 'partial',
                        },
                        session.get('deep_draft_id'),
                    )
                    if draft_id:
                        session['deep_draft_id'] = draft_id
                else:
                    delete_user_record(session.get('deep_draft_id'), user_email)
            session.pop('deep_draft_id', None)
            session.pop('guest_mode', None)
            if 'user' in session:
                return redirect(url_for('dashboard'))
            return redirect(url_for('home'))

        # debug print
        app.logger.debug(f"Received deep POST: user_name={user_name!r}, user_email={user_email!r}, name={name!r}, purpose={purpose!r}, type={proc_type!r}")
        missing = []
        if not is_guest and not user_name:
            missing.append('your name')
        if not is_guest and not user_email:
            missing.append('your email')
        if not name:
            missing.append('process name')
        if not description:
            missing.append('description')
        if proc_type not in PROCESS_TEMPLATES:
            missing.append('process type')
        if missing:
            if not is_guest and 'user' in session:
                draft_id = upsert_partial_record(
                    current_user(),
                    'deep',
                    {
                        'name': name,
                        'purpose': purpose,
                        'type': proc_type if proc_type in PROCESS_TEMPLATES else '',
                        'steps': parse_process_description_steps(description),
                        'description': description,
                        'answers': {},
                        'score': None,
                        'status': 'partial',
                    },
                    session.get('deep_draft_id'),
                )
                if draft_id:
                    session['deep_draft_id'] = draft_id
            error = 'Please provide ' + ', '.join(missing) + '.'
            return render_template('deep.html', error=error, templates=PROCESS_TEMPLATES,
                                    name=name, description=description,
                                    selected_type=proc_type,
                                    user_name=user_name, user_email=user_email, values=answers)
        
        # Validate answers
        missing_answers = []
        for q in PROCESS_TEMPLATES[proc_type]['questions']:
            key = q['key']
            if not answers.get(key):
                missing_answers.append(q['text'])
        
        if missing_answers:
            if not is_guest and 'user' in session:
                draft_id = upsert_partial_record(
                    current_user(),
                    'deep',
                    {
                        'name': name,
                        'purpose': purpose,
                        'type': proc_type,
                        'steps': parse_process_description_steps(description),
                        'description': description,
                        'answers': answers,
                        'score': None,
                        'status': 'partial',
                    },
                    session.get('deep_draft_id'),
                )
                if draft_id:
                    session['deep_draft_id'] = draft_id
            error = 'Please answer all required questions.'
            return render_template('deep.html', error=error, templates=PROCESS_TEMPLATES,
                                    name=name, description=description,
                                    selected_type=proc_type,
                                    user_name=user_name, user_email=user_email, values=answers)
        
        score_info = score_answers(proc_type, answers)
        percent = score_info['percent']
        rec = score_info['recommendation']
        record = {
            'id': session.get('deep_draft_id') or next_submission_id(),
            'created': current_date_string(),
            'updated': current_date_string(),
            'path': 'deep',
            'user': {'name': user_name, 'email': user_email},
            'name': name,
            'purpose': purpose,
            'type': proc_type,
            'steps': parse_process_description_steps(description),
            'description': description,
            'answers': answers,
            'score': score_info,
            'status': 'submitted',
        }
        # persist only for logged-in users; guest evaluations are ephemeral
        if not session.get('guest_mode', False):
            draft_id = session.get('deep_draft_id')
            upsert_partial_record(current_user(), 'deep', record, draft_id)
            session.pop('deep_draft_id', None)
            return render_template('deep_result.html', record=record, percent=percent, recommendation=rec)
        else:
            # store guest result in session temporarily so they can save it after login
            session['guest_result'] = record
            session.pop('guest_mode', None)
            return render_template('deep_result.html', record=record, percent=percent, recommendation=rec, is_guest=True)
    # include session user if available
    user = session.get('user', {})
    return render_template('deep.html', templates=PROCESS_TEMPLATES,
                           user_name=user.get('name',''),
                           user_email=user.get('email',''),
                           is_guest=session.get('guest_mode', False))

@app.route('/quick/save-guest', methods=['POST'])
def quick_save_guest():
    guest_result = session.get('guest_result')
    if not isinstance(guest_result, dict):
        flash('No guest triage result was found to save. Please run Quick Look first.')
        return redirect(url_for('quick_start'))

    first_name = request.form.get('first_name', '').strip()
    last_name = request.form.get('last_name', '').strip()
    email = normalize_email(request.form.get('email', ''))
    password = request.form.get('password', '')
    password_confirm = request.form.get('password_confirm', '')

    if not first_name or not last_name:
        error = 'Please provide both first and last name.'
        return render_template(
            'quick_result.html',
            record=guest_result,
            percent=(guest_result.get('score') or {}).get('percent', 0),
            recommendation=recommendation_from_percent((guest_result.get('score') or {}).get('percent', 0)),
            is_guest=True,
            error=error,
            first_name=first_name,
            last_name=last_name,
            email=email,
        )
    if not is_valid_email(email):
        error = 'Please provide a valid email address.'
        return render_template(
            'quick_result.html',
            record=guest_result,
            percent=(guest_result.get('score') or {}).get('percent', 0),
            recommendation=recommendation_from_percent((guest_result.get('score') or {}).get('percent', 0)),
            is_guest=True,
            error=error,
            first_name=first_name,
            last_name=last_name,
            email=email,
        )
    if password != password_confirm:
        error = 'Passwords do not match.'
        return render_template(
            'quick_result.html',
            record=guest_result,
            percent=(guest_result.get('score') or {}).get('percent', 0),
            recommendation=recommendation_from_percent((guest_result.get('score') or {}).get('percent', 0)),
            is_guest=True,
            error=error,
            first_name=first_name,
            last_name=last_name,
            email=email,
        )

    created, message = create_user_account(first_name, last_name, email, password)
    if not created:
        return render_template(
            'quick_result.html',
            record=guest_result,
            percent=(guest_result.get('score') or {}).get('percent', 0),
            recommendation=recommendation_from_percent((guest_result.get('score') or {}).get('percent', 0)),
            is_guest=True,
            error=message,
            first_name=first_name,
            last_name=last_name,
            email=email,
        )

    user = find_user_by_email(email)
    if user:
        session.permanent = False
        login_session_from_user(user)
        session['is_admin'] = is_admin_email(user.get('email', ''))
        if persist_guest_result_for_user(current_user()):
            flash('Account created and your triage was saved.')

    return redirect(url_for('dashboard'))

@app.route('/items')
def items():
    if 'user' not in session:
        return redirect(url_for('user_info'))
    user = current_user()
    purge_expired_user_deleted_records()
    try:
        items_data = load_data()
        if not isinstance(items_data, list):
            items_data = []
    except (FileNotFoundError, ValueError):
        items_data = []
    user_records = [r for r in items_data if r.get('user', {}).get('email') == user.get('email')]
    active_items = [r for r in user_records if not is_soft_deleted_record(r)]
    deleted_items = [r for r in user_records if is_soft_deleted_record(r)]
    active_items.sort(key=lambda r: r.get('updated') or r.get('created') or '', reverse=True)
    deleted_items.sort(key=lambda r: r.get('deleted_at') or r.get('updated') or '', reverse=True)
    return render_template(
        'items.html',
        items=active_items,
        deleted_items=deleted_items,
        retention_days=SOFT_DELETE_RETENTION_DAYS,
    )

if __name__ == '__main__':
    # run the development server when executed directly
    # allow PORT env var override (ports like 5000 may be occupied on macOS)
    import os
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.environ.get('FLASK_DEBUG', '0') == '1'
    print(f"Starting development server on http://127.0.0.1:{port}/")
    app.run(debug=debug_mode, host='0.0.0.0', port=port, use_reloader=False, threaded=True)