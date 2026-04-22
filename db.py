from __future__ import annotations

import atexit
import json
import os
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, func, inspect, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from models import Assessment, Base, Feedback, User

BASE_DIR = os.path.dirname(__file__)
DEFAULT_SQLITE_URL = f"sqlite:///{os.path.join(BASE_DIR, 'project.db')}"

_ENGINE = None
_SESSION_FACTORY = None
_INITIALIZED = False
_BACKUP_CREATED = False


def normalize_database_url(url: str) -> str:
    if not url:
        return DEFAULT_SQLITE_URL
    if url.startswith('postgres://'):
        return 'postgresql://' + url[len('postgres://'):]
    return url


def get_database_url() -> str:
    return normalize_database_url(os.environ.get('DATABASE_URL', '').strip())


def get_sqlite_database_path() -> str:
    url = get_database_url()
    if not url.startswith('sqlite:///'):
        return ''
    return url[len('sqlite:///'):]


def backup_sqlite_database(reason: str) -> str | None:
    global _BACKUP_CREATED
    if _BACKUP_CREATED:
        return None

    database_path = get_sqlite_database_path()
    if not database_path or not os.path.exists(database_path):
        return None

    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    backup_path = f'{database_path}.backup-{stamp}.sqlite'
    shutil.copy2(database_path, backup_path)
    _BACKUP_CREATED = True
    return backup_path


def get_engine():
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = create_engine(get_database_url(), future=True)
    return _ENGINE


def dispose_engine() -> None:
    global _ENGINE, _SESSION_FACTORY
    if _ENGINE is not None:
        _ENGINE.dispose()
        _ENGINE = None
    _SESSION_FACTORY = None


atexit.register(dispose_engine)


def get_session_factory():
    global _SESSION_FACTORY
    if _SESSION_FACTORY is None:
        _SESSION_FACTORY = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, future=True)
    return _SESSION_FACTORY


@contextmanager
def session_scope():
    session: Session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _assessment_payload_to_row(
    record: dict[str, Any],
    row: Assessment | None = None,
    users_by_email: dict[str, int] | None = None,
) -> Assessment:
    row = row or Assessment()
    record_id = str(record.get('id', '')).strip()
    row.record_id = record_id
    user_payload = record.get('user', {}) if isinstance(record.get('user', {}), dict) else {}
    user_email = str(user_payload.get('email', '')).strip().lower()
    row.user_email = user_email
    raw_user_id = user_payload.get('id')
    parsed_user_id = None
    if isinstance(raw_user_id, int):
        parsed_user_id = raw_user_id
    elif isinstance(raw_user_id, str) and raw_user_id.strip().isdigit():
        parsed_user_id = int(raw_user_id.strip())
    elif user_email and users_by_email:
        parsed_user_id = users_by_email.get(user_email)
    row.user_id = parsed_user_id
    row.path = str(record.get('path', '') or '')
    row.status = str(record.get('status', 'partial') or 'partial')
    row.name = str(record.get('name', '') or '')
    row.purpose = str(record.get('purpose', '') or '')
    row.process_type = str(record.get('type', '') or '')
    row.deep_dive_complete = bool(record.get('deep_dive_complete', False))
    row.created = str(record.get('created', '') or '')
    row.updated = str(record.get('updated', '') or '')
    row.payload = json.dumps(record, ensure_ascii=False)
    return row


def _row_to_assessment_payload(row: Assessment) -> dict[str, Any]:
    try:
        payload = json.loads(row.payload) if row.payload else {}
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if not payload.get('id'):
        payload['id'] = row.record_id
    if not payload.get('path'):
        payload['path'] = row.path
    if not payload.get('status'):
        payload['status'] = row.status
    if 'user' not in payload:
        payload['user'] = {'email': row.user_email}
    return payload


def load_assessments() -> list[dict[str, Any]]:
    with session_scope() as session:
        rows = session.execute(select(Assessment).order_by(Assessment.id.asc())).scalars().all()
        return [_row_to_assessment_payload(row) for row in rows]


def save_assessments(records: list[dict[str, Any]]) -> None:
    normalized: list[dict[str, Any]] = []
    keep_ids: set[str] = set()

    for item in records:
        if not isinstance(item, dict):
            continue
        record_id = str(item.get('id', '')).strip()
        if not record_id:
            continue
        normalized.append(item)
        keep_ids.add(record_id)

    with session_scope() as session:
        existing_rows = session.execute(select(Assessment)).scalars().all()
        existing_by_record_id = {row.record_id: row for row in existing_rows}
        users_by_email = {
            str(row.email).strip().lower(): int(row.id)
            for row in session.execute(select(User.id, User.email)).all()
            if row.email
        }

        for record in normalized:
            record_id = str(record.get('id', '')).strip()
            row = existing_by_record_id.get(record_id)
            row = _assessment_payload_to_row(record, row=row, users_by_email=users_by_email)
            session.add(row)

        for row in existing_rows:
            if row.record_id not in keep_ids:
                session.delete(row)


def load_users() -> list[dict[str, Any]]:
    with session_scope() as session:
        rows = session.execute(select(User).order_by(User.id.asc())).scalars().all()
        return [
            {
                'id': row.id,
                'first_name': row.first_name,
                'last_name': row.last_name,
                'name': row.name,
                'email': row.email,
                'password_hash': row.password_hash,
                'created': row.created,
            }
            for row in rows
        ]


def create_feedback_entry(user: dict[str, Any], feedback_type: str, message: str) -> int:
    with session_scope() as session:
        row = Feedback(
            user_id=user.get('id') if isinstance(user.get('id'), int) else None,
            user_email=str(user.get('email', '') or '').strip().lower(),
            user_name=str(user.get('name', '') or '').strip(),
            feedback_type=str(feedback_type or 'idea').strip().lower(),
            message=str(message or '').strip(),
        )
        session.add(row)
        session.flush()
        return int(row.id)


def list_feedback_entries(limit: int = 100) -> list[dict[str, Any]]:
    safe_limit = max(10, min(limit, 500))
    with session_scope() as session:
        rows = session.execute(select(Feedback).order_by(Feedback.id.desc()).limit(safe_limit)).scalars().all()
        return [
            {
                'id': row.id,
                'user_name': row.user_name,
                'user_email': row.user_email,
                'feedback_type': row.feedback_type,
                'message': row.message,
                'created_at': row.created_at.isoformat() if row.created_at else '',
            }
            for row in rows
        ]


def ensure_assessments_ownership_fk() -> None:
    engine = get_engine()
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if 'assessments' not in table_names or 'users' not in table_names:
        return

    column_names = {col.get('name') for col in inspector.get_columns('assessments')}
    foreign_keys = inspector.get_foreign_keys('assessments')
    has_user_id = 'user_id' in column_names
    has_user_fk = any(
        fk.get('referred_table') == 'users' and 'user_id' in (fk.get('constrained_columns') or [])
        for fk in foreign_keys
    )

    sqlite_backend = get_database_url().startswith('sqlite:///')

    with engine.begin() as conn:
        if sqlite_backend and not has_user_fk:
            backup_sqlite_database('ownership_fk_rebuild')
            conn.execute(text('PRAGMA foreign_keys=OFF'))
            try:
                conn.execute(text('ALTER TABLE assessments RENAME TO assessments_old'))
                conn.execute(
                    text(
                        'CREATE TABLE assessments ('
                        'id INTEGER NOT NULL PRIMARY KEY, '
                        'record_id VARCHAR(32) NOT NULL UNIQUE, '
                        'user_id INTEGER NULL, '
                        'user_email VARCHAR(320) NOT NULL DEFAULT "", '
                        'path VARCHAR(24) NOT NULL DEFAULT "", '
                        'status VARCHAR(24) NOT NULL DEFAULT "partial", '
                        'name VARCHAR(255) NOT NULL DEFAULT "", '
                        'purpose TEXT NOT NULL DEFAULT "", '
                        'process_type VARCHAR(16) NOT NULL DEFAULT "", '
                        'deep_dive_complete BOOLEAN NOT NULL DEFAULT 0, '
                        'created VARCHAR(32) NOT NULL DEFAULT "", '
                        'updated VARCHAR(32) NOT NULL DEFAULT "", '
                        'payload TEXT NOT NULL DEFAULT "{}", '
                        'created_at DATETIME NOT NULL, '
                        'updated_at DATETIME NOT NULL, '
                        'FOREIGN KEY(user_id) REFERENCES users(id)'
                        ')'
                    )
                )

                user_id_select = (
                    'COALESCE(a.user_id, (SELECT users.id FROM users WHERE lower(users.email) = lower(a.user_email) LIMIT 1))'
                    if has_user_id
                    else '(SELECT users.id FROM users WHERE lower(users.email) = lower(a.user_email) LIMIT 1)'
                )
                conn.execute(
                    text(
                        'INSERT INTO assessments ('
                        'id, record_id, user_id, user_email, path, status, name, purpose, process_type, '
                        'deep_dive_complete, created, updated, payload, created_at, updated_at'
                        ') '
                        'SELECT '
                        'a.id, a.record_id, ' + user_id_select + ', a.user_email, a.path, a.status, a.name, a.purpose, '
                        'a.process_type, a.deep_dive_complete, a.created, a.updated, a.payload, a.created_at, a.updated_at '
                        'FROM assessments_old a'
                    )
                )
                conn.execute(text('DROP TABLE assessments_old'))
            finally:
                conn.execute(text('PRAGMA foreign_keys=ON'))

        conn.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS ix_assessments_record_id ON assessments (record_id)'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS ix_assessments_user_email ON assessments (user_email)'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS ix_assessments_user_id ON assessments (user_id)'))
        conn.execute(
            text(
                'UPDATE assessments '
                'SET user_id = (SELECT users.id FROM users WHERE lower(users.email) = lower(assessments.user_email) LIMIT 1) '
                'WHERE user_id IS NULL AND user_email IS NOT NULL AND user_email <> ""'
            )
        )


def save_users(users: list[dict[str, Any]]) -> None:
    normalized: list[dict[str, Any]] = []
    keep_emails: set[str] = set()

    for user in users:
        if not isinstance(user, dict):
            continue
        email = str(user.get('email', '')).strip().lower()
        if not email:
            continue
        user = dict(user)
        user['email'] = email
        normalized.append(user)
        keep_emails.add(email)

    with session_scope() as session:
        existing_rows = session.execute(select(User)).scalars().all()
        existing_by_email = {row.email.lower(): row for row in existing_rows}

        for user in normalized:
            email = user['email']
            row = existing_by_email.get(email)
            if row is None:
                row = User(email=email)
            row.first_name = str(user.get('first_name', '') or '')
            row.last_name = str(user.get('last_name', '') or '')
            row.name = str(user.get('name', '') or '')
            row.password_hash = str(user.get('password_hash', '') or '')
            row.created = str(user.get('created', '') or '')
            session.add(row)

        for row in existing_rows:
            if row.email.lower() not in keep_emails:
                session.delete(row)


def _read_json_file(path: str) -> list[dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def migrate_from_json_files() -> bool:
    users_path = os.path.join(BASE_DIR, 'users_store.json')
    assessments_path = os.path.join(BASE_DIR, 'data_store.json')

    with session_scope() as session:
        existing_users = session.execute(select(User.id)).first() is not None
        existing_assessments = session.execute(select(Assessment.id)).first() is not None

    migrated = False

    if not existing_users:
        users = _read_json_file(users_path)
        if users:
            save_users(users)
            migrated = True

    if not existing_assessments:
        assessments = _read_json_file(assessments_path)
        if assessments:
            save_assessments(assessments)
            migrated = True

    return migrated


def init_database() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    Base.metadata.create_all(get_engine())
    ensure_assessments_ownership_fk()
    migrate_from_json_files()
    _INITIALIZED = True


def get_database_backend_label() -> str:
    url = get_database_url()
    if url.startswith('postgresql://'):
        return 'PostgreSQL'
    if url.startswith('sqlite:///'):
        return 'SQLite'
    return 'SQL'


def get_admin_snapshot(limit: int = 100, user_search: str = '') -> dict[str, Any]:
    safe_limit = max(10, min(limit, 500))
    search_value = str(user_search or '').strip().lower()
    with session_scope() as session:
        users_query = select(User)
        if search_value:
            pattern = f'%{search_value}%'
            users_query = users_query.where(
                or_(
                    func.lower(User.email).like(pattern),
                    func.lower(User.name).like(pattern),
                    func.lower(User.first_name).like(pattern),
                    func.lower(User.last_name).like(pattern),
                )
            )

        users = session.execute(users_query.order_by(User.id.desc()).limit(safe_limit)).scalars().all()
        assessments = session.execute(select(Assessment).order_by(Assessment.id.desc()).limit(safe_limit)).scalars().all()
        feedback_rows = session.execute(select(Feedback).order_by(Feedback.id.desc()).limit(safe_limit)).scalars().all()
        matched_users_count = len(session.execute(users_query.with_only_columns(User.id)).all())

        return {
            'users_count': len(session.execute(select(User.id)).all()),
            'matched_users_count': matched_users_count,
            'assessments_count': len(session.execute(select(Assessment.id)).all()),
            'feedback_count': len(session.execute(select(Feedback.id)).all()),
            'users': [
                {
                    'id': row.id,
                    'name': row.name,
                    'email': row.email,
                    'created': row.created,
                }
                for row in users
            ],
            'assessments': [
                {
                    'id': row.record_id,
                    'user_email': row.user_email,
                    'path': row.path,
                    'status': row.status,
                    'name': row.name,
                    'updated': row.updated,
                }
                for row in assessments
            ],
            'feedback': [
                {
                    'id': row.id,
                    'user_name': row.user_name,
                    'user_email': row.user_email,
                    'feedback_type': row.feedback_type,
                    'message': row.message,
                    'created_at': row.created_at.isoformat() if row.created_at else '',
                }
                for row in feedback_rows
            ],
            'backend': get_database_backend_label(),
            'database_url': get_database_url(),
        }
