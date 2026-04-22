# Production Deployment Checklist

## Environment Configuration

### Required Environment Variables (Production)

**Security & Flask:**
- `APP_ENV=production` — Must be set to activate production validation
- `FLASK_SECRET_KEY` — Minimum 32 random characters (generated if not provided in dev)
- `SESSION_COOKIE_SECURE=1` — Required in production (HTTPS only)
- `FLASK_DEBUG=0` — Debug mode must be off

**Database:**
- `DATABASE_URL` — PostgreSQL connection string for hosted deployment
  - Local dev: `sqlite:///project.db`
  - Heroku: `postgresql://user:pass@host/dbname` (auto-provided)
  - AWS RDS: `postgresql://user:pass@rds-host:5432/dbname`

**Admin Access:**
- `ADMIN_EMAILS` — Comma-separated list of admin user emails OR
- `ADMIN_LOGIN_EMAIL` + `ADMIN_LOGIN_PASSWORD` — Bootstrap admin at startup (both required together)

**Email (Optional):**
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM` — For password reset emails
- `SHOW_RESET_CODE=0` — Hide test reset codes in production (default is 1 for dev)

**AI Features (Optional):**
- `MISTRAL_API_KEY` — For live AI-powered deep discussions (leave empty for rule-based fallback)
- `MISTRAL_MODEL` — Default: `mistral-large-latest`

### Startup Validation

When `APP_ENV=production`, the app runs security checks and **fails fast** if:
- `FLASK_SECRET_KEY` is missing, too short (<32 chars), or uses placeholder text
- `SESSION_COOKIE_SECURE` is not set to `1`
- `ADMIN_LOGIN_EMAIL`/`ADMIN_LOGIN_PASSWORD` are partially configured
- Admin credentials fail password policy checks

## Pre-Deployment Tasks

### ✅ Code Quality
- [x] No debug print statements (removed debug comment on line 3325)
- [x] No console.log calls (console.error is acceptable for error tracking)
- [x] No hardcoded secrets or API keys
- [x] All imports are used and current
- [x] No temporary/test files in production build

### ✅ Configuration Files
- [x] `.env.example` is complete and includes all production variables
- [x] `.gitignore` includes legacy files, backups, and sensitive data
- [x] `requirements.txt` is up to date with all dependencies
- [x] `wsgi.py` is production-ready (Gunicorn entry point)

### ✅ Documentation
- [x] `README.md` includes AI features, process maps, and live discussion mode
- [x] `SECURITY.md` documents all security controls
- [x] `API_DOCUMENTATION.md` is current with REST API details

### ✅ Database
- [x] Schema migrations handled by SQLAlchemy models
- [x] Automatic migration from legacy JSON files on first run (if needed)
- [x] Soft-delete retention and purge policies implemented
- [x] Database backups created before ownership migrations (SQLite)

### ✅ Security
- [x] Passwords hashed with Werkzeug (never plain text)
- [x] Session cookies hardened (HttpOnly, SameSite, Secure flag)
- [x] User ownership checks prevent data leakage
- [x] Email validation enforced
- [x] Password complexity policy enforced
- [x] Admin-only routes are protected
- [x] CSRF protection via Flask sessions

### ✅ Features
- [x] Quick Look assessment flow functional
- [x] Deep Evaluation with LLM integration (with fallback)
- [x] Live process map visualization with Mermaid
- [x] Structured recommendations with step traceability
- [x] Markdown rendering in chat messages
- [x] User dashboard and submission history
- [x] REST API with authentication
- [x] Admin database inspection page

## Deployment Steps

### 1. Environment Setup
```bash
# Create production .env file (use .env.example as template)
cp .env.example .env

# Edit .env with production values
export APP_ENV=production
export FLASK_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export SESSION_COOKIE_SECURE=1
export DATABASE_URL="postgresql://..."  # or from Heroku
export ADMIN_EMAILS="admin@yourorg.com"
export MISTRAL_API_KEY="your-key-here"  # optional
export SHOW_RESET_CODE=0  # hide test codes
```

### 2. Database Migration
```bash
# If using PostgreSQL, ensure connection is valid
python -c "from db import init_database; init_database()"
```

### 3. WSGI Server Launch
```bash
# Using Gunicorn (recommended for production)
gunicorn -w 4 -b 0.0.0.0:5000 wsgi:app

# With environment reload on file changes (optional)
gunicorn -w 4 --reload -b 0.0.0.0:5000 wsgi:app
```

### 4. Reverse Proxy Setup (Nginx/Apache)
- Forward requests to localhost:5000
- Set `X-Forwarded-For`, `X-Forwarded-Proto` headers
- Enable HTTPS/TLS
- Set `SESSION_COOKIE_SECURE=1` in .env

### 5. Monitoring & Logs
- Monitor app logs for startup validation errors
- Set up log aggregation (syslog, CloudWatch, etc.)
- Monitor database connection health
- Track LLM API failures (logged at WARNING level)

## Verification Checklist

Before going live:
- [ ] All required environment variables are set and validated
- [ ] Database is reachable and tables are created
- [ ] Admin account is accessible
- [ ] HTTPS/TLS is enabled (SESSION_COOKIE_SECURE=1)
- [ ] Email service is functional (if password reset enabled)
- [ ] LLM API key is valid (if AI features enabled)
- [ ] No sensitive data in git history
- [ ] Backups are configured
- [ ] Log aggregation is working
- [ ] Application runs without startup errors

## Post-Deployment

### Health Checks
- Verify `/` loads (home page)
- Test login flow with admin account
- Test quick look assessment
- Test deep evaluation (if Mistral key provided)
- Verify `/admin/db` shows correct data

### Monitoring
- Monitor error rates and latency
- Track failed LLM calls
- Monitor database query performance
- Check disk space (SQLite) or database quota (PostgreSQL)
- Review logs for security-related events

### Maintenance
- Review soft-deleted records periodically (730-day retention)
- Purge expired soft-deleted records when appropriate
- Monitor Mistral API usage and quota
- Keep dependencies updated (but test before deploying)
