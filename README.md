Process Triage (Simplified)

A Flask web application for scoring and capturing process assessments. Users can evaluate processes using a quick look or deep evaluation, with user authentication, guest mode, and SQL-backed persistent storage.

## Features

- **User Authentication**: Sign up, login, password reset
- **Access Control**: Registration and login required for assessment flows
- **Quick Look**: Two-page questionnaire for rapid process assessment
- **Deep Evaluation**: More comprehensive assessment requiring login
- **Live Discussion Mode**: Chat-based deep dive with an AI assistant powered by Mistral LLM
- **Process Map Visualization**: Mermaid flowcharts showing process steps, microsteps, and improvement suggestions
- **Live Map Building**: Process map updates in real-time during discussion as the assistant learns more
- **Structured Recommendations**: AI-generated improvement suggestions with step-level traceability (e.g., `[R1 -> S2]`)
- **Markdown Chat Rendering**: Chat messages preserve formatting—bullets, headings, and numbered lists display cleanly
- **Dashboard**: View and edit saved assessments
- **Scoring System**: 8 weighted questions that generate a priority recommendation (High/Medium/Low priority)
- **SQL Persistence**: Users and assessments stored in SQL (SQLite locally, PostgreSQL on Heroku)
- **Automatic Migration**: Existing `data_store.json` and `users_store.json` records are imported on first run
- **Admin Database View**: Administrator-friendly in-app database inspection (`/admin/db`)
- **Process Types**: Support for three process types (C, R, D)
- **REST API**: Programmatic access to assessment data via `/api/v1` endpoints

## REST API

The application provides a REST API for accessing assessment data from external programs, websites, or tools.

### Quick API Example

```bash
# 1. Sign in (stores session cookie)
curl -c cookies.txt -d "email=user@example.com&password=MyPassword123" \
  http://localhost:5000/user/info

# 2. List all your assessments
curl -b cookies.txt http://localhost:5000/api/v1/assessments

# 3. Get a specific assessment
curl -b cookies.txt http://localhost:5000/api/v1/assessments/S001
```

### API Endpoints

- **GET** `/api/v1/assessments` — List all active assessments for the current user
- **GET** `/api/v1/assessments/<id>` — Get a specific assessment by ID

All endpoints require authentication and return JSON. For complete API documentation, see [API_DOCUMENTATION.md](API_DOCUMENTATION.md).

## AI-Powered Deep Discussion & Process Maps

### Live Discussion Mode

After completing a deep evaluation questionnaire, users can engage in a guided discussion with an AI assistant (powered by Mistral LLM). The assistant:

- Asks clarifying questions to understand the process better
- Builds a visual process map in real-time as the discussion progresses
- Stops asking questions when you request recommendations or indicate you've provided all available details
- Formats responses with proper markdown (bullets, headings, numbered lists)

**Key behaviors:**
- The map acts as the "review" — the assistant respects the process map as the source of truth
- When recommendations are requested, the assistant provides structured improvement suggestions instead of continuing to probe
- All chat messages support markdown formatting for readability

### Process Map Visualization

Each discussion generates a **live Mermaid flowchart** showing:

- **Main steps**: Core process flow
- **Microsteps**: Detailed sub-tasks grouped under each main step
- **Improvement lane**: AI-suggested improvements at the bottom with step-level traceability (e.g., `[R1 -> S2]` means recommendation 1 applies to step S2)
- **Scrollable viewport**: Both horizontal and vertical scrollbars allow panning a large diagram without expanding the page layout
- **Large, readable text**: Node labels are sized for easy reading in the scrollable window
- **Live updates**: Map re-renders automatically as the discussion evolves

### Structured Recommendations

When you ask for recommendations, the assistant returns:

1. **Numbered main steps** with microstep bullets below each
2. **Step-level traceability tags** like `[R# -> S#]` linking each recommendation to a process step
3. **Expected benefits** for each recommendation
4. **Priority-based ordering** for quick decision-making

Example output:
```
Structured Process Review
1) Intake request
   - Validate required fields
2) Manager approval
   - Escalate exceptions

Recommended Improvements (Prioritized)
1) [R1 -> S1] For 'Intake request', convert the repeated micro-steps into a short checklist...
2) [R2 -> S2] For 'Manager approval', define required input fields...
```

## Quick Start

1. Create and activate a virtual environment (macOS/Linux):

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Configure environment variables:

```bash
cp .env.example .env
```

Edit `.env` with your values (especially `FLASK_SECRET_KEY`, admin settings, and any SMTP/AI keys).

**LLM/AI Configuration (optional):**
To enable live discussion with AI-powered process mapping, set:
```bash
MISTRAL_API_KEY=your_mistral_api_key_here
```

If `MISTRAL_API_KEY` is not set, the app will use a fallback rule-based discussion system (discussions will still work, but without live LLM responses).

**Security in Production:**
When `APP_ENV=production`, startup includes strict security validation. The app will fail fast if critical settings are unsafe or missing (for example, missing/weak `FLASK_SECRET_KEY`, `SESSION_COOKIE_SECURE!=1`, or incomplete admin bootstrap credentials).

4. Run the Flask app:

```bash
python Web_app.py
```

The app will start on `http://127.0.0.1:5000`

To use a different port, set the PORT environment variable:

```bash
PORT=8000 python Web_app.py
```

## WSGI (Gunicorn) Run

For production-style serving with WSGI:

```bash
gunicorn -w 2 -b 0.0.0.0:${PORT:-5000} wsgi:app
```

The `wsgi.py` module exposes `app` for Gunicorn.

## File Structure

- `Web_app.py` — Main Flask application with all routes and admin logic
- `app.py` — Core scoring logic and process templates
- `api.py` — REST API blueprint with `/api/v1` endpoints
- `db.py` — Database engine/session utilities, migration, and admin snapshots
- `models.py` — SQLAlchemy ORM models (`users`, `assessments`)
- `project.db` — Local SQLite database file (auto-created)
- `data_store.json` — Legacy JSON file (auto-migrated into SQL)
- `users_store.json` — Legacy JSON users file (auto-migrated into SQL)
- `API_DOCUMENTATION.md` — Complete REST API reference
- `SECURITY.md` — Security controls and best practices
- `templates/` — Jinja2 HTML templates
  - `welcome.html` — Landing page for unauthenticated users
  - `user.html` — Sign up and login
  - `reset_password.html` — Password reset
  - `quick_start.html` — First page of quick look (process details)
  - `quick.html` — Second page of quick look (questions)
  - `quick_result.html` — Quick look results summary
  - `deep.html` — Deep evaluation questionnaire
  - `deep_result.html` — Deep evaluation results summary
  - `discussion.html` — Live discussion mode with AI chat and real-time process map
  - `dashboard.html` — User's assessment history
  - `record/<id>` — View specific assessment

## The Question System

Both Quick Look and Deep Evaluation use 8 contextual questions:

1. **Frequency**: How often does this process happen?
2. **Involvement**: Who typically participates? (multi-select)
3. **Frustration**: Does this process involve frustration, delays, or workarounds?
4. **Impact**: If this process fails or is done incorrectly, what's the impact?
5. **Consistency**: Is this process done the same way every time?
6. **Tools**: How many tools or systems are typically used?
7. **Flagged**: Has this process been discussed as an issue before?
8. **Benefits**: What would improving this process most likely improve? (multi-select)

Each question includes an explanation of why the answer matters for prioritization.

## Scoring & Recommendations

Scores are calculated from the 8 questions with weighted points:
- Frequency: 15 points
- Involvement: 15 points
- Frustration: 20 points
- Impact: 20 points
- Consistency: 10 points
- Tools: 10 points
- Flagged: 10 points
- Benefits: 10 point bonus

**Recommendation Tiers**:
- 70%+: **High Priority**
- 50-70%: **Medium Priority**
- 30-50%: **Low-Medium Priority**
- <30%: **Low Priority**

## Authentication & Persistence

- **Logged-in users**: All assessments are saved to disk and visible in their dashboard
- **Logged-in users**: All users and assessments are stored in SQL and visible in their dashboard
- **Assessment routes**: Quick and deep evaluation flows require login
- **Data ownership**: Users can only view and modify their own records

## Security Overview

- Passwords are hashed using Werkzeug security utilities (`generate_password_hash` / `check_password_hash`)
- Session cookies are hardened (`HttpOnly`, `SameSite=Lax`, optional `Secure` in HTTPS)
- Users must be authenticated to access protected routes
- Ownership checks prevent users from viewing or modifying other users' records
- Email input is normalized and validated
- Password complexity policy is enforced in account and reset flows
- User-initiated deletion is soft delete with retention and controlled purge
- On first SQLite ownership migration, the app creates an automatic backup of `project.db` before rebuilding the table

For complete implementation details, see [SECURITY.md](SECURITY.md).

## Database Setup (Heroku + AWS path)

This project is configured for a seamless Heroku launch with easy future migration to AWS:

- **Local development**: SQLite file (`project.db`) via `sqlite:///project.db`
- **Heroku production**: PostgreSQL via `DATABASE_URL`
- **Future AWS migration**: Point `DATABASE_URL` to Amazon RDS PostgreSQL

Configuration:

```bash
# local (optional override)
DATABASE_URL=sqlite:///project.db

# Heroku/AWS
DATABASE_URL=postgresql://user:pass@host:5432/dbname
```

Notes:
- Heroku may provide `postgres://...`; the app normalizes this to `postgresql://...` automatically.
- On startup, tables are created and legacy JSON data is migrated only if SQL tables are empty.

## Admin Access

Set admin emails (comma-separated) to access `/admin/db`:

```bash
ADMIN_EMAILS=admin1@example.com,admin2@example.com
```

The admin view shows:
- Database backend type
- User and assessment counts
- Recent users and assessments

## Routes

- `/` — Welcome page (unauthenticated) or home page (authenticated)
- `/user` — Sign up and login
- `/logout` — Logout
- `/reset-password` — Password reset form
- `/dashboard` — User's assessment history
- `/admin/db` — Admin database inspection page (requires admin email)
- `/record/<id>` — View/edit specific assessment
- `/record/<id>/discussion` — Live discussion mode for deep evaluation with AI and process map
- `/quick_start` — Quick look page 1 (process details)
- `/quick` — Quick look page 2 (questions)
- `/deep` — Deep evaluation questionnaire

## Port Configuration

By default, the app runs on port 5000. On macOS, this port may be blocked by AirPlay Receiver. To use a different port:

```bash
PORT=5001 python Web_app.py
```

Or use `lsof` to check what's using port 5000:

```bash
lsof -nP -iTCP:5000 -sTCP:LISTEN
```

## Persistence

- Primary persistence is SQL (`project.db` locally, PostgreSQL in hosted environments)
- Legacy JSON files are supported only as one-time migration sources
- Each assessment includes user info, process details, answers, score, recommendation, and chat context
- Guest assessments are not persisted
