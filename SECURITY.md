# Program Security

This document describes how user data is protected in this application and what controls are in place for authentication, authorization, session management, and input validation.

## Security Controls Implemented

### 1. Password Protection
- Passwords are never stored in plain text.
- Passwords are hashed using Werkzeug security helpers:
  - `generate_password_hash(...)` for storage
  - `check_password_hash(...)` for verification
- Password rules are enforced before account creation and password reset/update:
  - Minimum length: 10 characters
  - At least one uppercase letter
  - At least one lowercase letter
  - At least one number

### 2. Authentication and Authorization
- Users must be authenticated before accessing protected data routes.
- Ownership checks ensure users can access or modify only their own records.
- Admin routes are protected by explicit admin checks (`require_admin`).
- Record view/edit/delete/restore/discussion routes enforce user session and ownership controls.

### 3. Session Security
- Flask session key is no longer hardcoded; it is sourced from `FLASK_SECRET_KEY` or generated securely at runtime.
- Session cookie hardening is enabled:
  - `SESSION_COOKIE_HTTPONLY=True`
  - `SESSION_COOKIE_SAMESITE='Lax'`
  - `SESSION_COOKIE_SECURE` configurable via environment (`SESSION_COOKIE_SECURE=1` in HTTPS environments)
- Session fixation mitigation:
  - Session is cleared before creating a login session (`session.clear()` in login flow)
  - Session is fully cleared on logout
- Session lifetime and refresh behavior are configured:
  - Non-permanent sessions
  - 1-hour lifetime for permanent session configuration
  - Session refresh disabled per request

### 4. Input Validation
- Email addresses are normalized and validated using a strict regex pattern.
- Validation is applied in:
  - account signup/login
  - guest account save flow
  - forgot-password and reset-password flows
- Required field checks are enforced across evaluation forms.
- Invalid or malformed input is rejected with user-safe error messages.

### 5. Data Access and Retention Protections
- Users cannot hard-delete their own submissions directly.
- User deletion is soft-delete with retention metadata:
  - `deleted_at`
  - `deleted_by='user'`
  - `hard_delete_after`
- Soft-deleted user records are purge-eligible after 730 days.
- Users can restore their own soft-deleted records before purge.
- Admin has dedicated retention audit visibility (queue, filters, urgency highlighting, sorting).
- Before the first destructive SQLite ownership migration, the app automatically creates a timestamped backup of `project.db`.

## Environment Recommendations for Production

Set these environment variables in production:
- `FLASK_SECRET_KEY`: long random secret value
- `SESSION_COOKIE_SECURE=1`: required when using HTTPS
- `FLASK_DEBUG=0`
- `ADMIN_LOGIN_PASSWORD`: strong non-default admin password
- `ADMIN_EMAILS`: explicit admin allow-list

## Security Summary

User data is protected through hashed passwords, stricter input validation, hardened session handling, enforced authentication/authorization, and safe soft-delete retention workflows with admin oversight.
