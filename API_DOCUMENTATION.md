# Process Triage API Documentation

## Overview

The Process Triage API provides REST endpoints for programmatic access to assessment data. All endpoints require user authentication via session cookies and return JSON responses.

**Base URL:** `/api/v1`

**Authentication:** All endpoints require an active authenticated session. Include session cookies from login.

## Endpoints

### List Assessments

**GET** `/api/v1/assessments`

Returns a JSON list of all non-deleted assessments belonging to the authenticated user.

#### Request

```bash
curl -b cookies.txt http://localhost:5000/api/v1/assessments
```

#### Response (200 OK)

```json
{
  "success": true,
  "count": 2,
  "data": [
    {
      "id": "S001",
      "name": "Customer Onboarding Process",
      "description": "New customer setup workflow",
      "process_type": "C",
      "path": "quick",
      "status": "complete",
      "deep_dive_complete": false,
      "created": "2026-01-15",
      "updated": "2026-01-15",
      "owner_email": "user@example.com"
    },
    {
      "id": "S002",
      "name": "Invoice Processing",
      "description": "Monthly invoicing workflow",
      "process_type": "R",
      "path": "deep",
      "status": "complete",
      "deep_dive_complete": true,
      "created": "2026-01-20",
      "updated": "2026-01-22",
      "owner_email": "user@example.com"
    }
  ]
}
```

#### Response (401 Unauthorized)

```json
{
  "error": "Unauthorized"
}
```

---

### Get Single Assessment

**GET** `/api/v1/assessments/<id>`

Returns a single assessment by ID. User must own the assessment.

#### Parameters

- `id` (path): Assessment record ID (e.g., `S001`)

#### Request

```bash
curl -b cookies.txt http://localhost:5000/api/v1/assessments/S001
```

#### Response (200 OK)

```json
{
  "success": true,
  "data": {
    "id": "S001",
    "name": "Customer Onboarding Process",
    "description": "New customer setup workflow",
    "process_type": "C",
    "path": "quick",
    "status": "complete",
    "deep_dive_complete": false,
    "created": "2026-01-15",
    "updated": "2026-01-15",
    "owner_email": "user@example.com"
  }
}
```

#### Response (401 Unauthorized)

```json
{
  "error": "Unauthorized"
}
```

#### Response (403 Forbidden)

```json
{
  "error": "Forbidden: You do not own this assessment"
}
```

#### Response (404 Not Found)

```json
{
  "error": "Assessment not found"
}
```

---

## Error Responses

All error responses follow this format:

```json
{
  "error": "Error description"
}
```

### HTTP Status Codes

| Code | Meaning | Common Causes |
|------|---------|---------------|
| 200 | OK | Successful request |
| 401 | Unauthorized | No valid session; user not logged in |
| 403 | Forbidden | User does not own the requested resource |
| 404 | Not Found | Assessment ID does not exist or is deleted |
| 405 | Method Not Allowed | Wrong HTTP method (e.g., POST to GET-only endpoint) |

---

## Authentication Flow

1. **Sign in** to the web application via `/user/info` (creates session cookie)
2. **Use session cookie** in API requests
3. **Include `-b cookies.txt`** in curl commands or set the `Cookie` header in your application

### Example: Login and API Access

```bash
# 1. Sign in (stores session in cookies.txt)
curl -c cookies.txt -d "email=user@example.com&password=MyPassword123" \
  http://localhost:5000/user/info

# 2. Use API with session
curl -b cookies.txt http://localhost:5000/api/v1/assessments
```

---

## Data Model

### Assessment Object

```json
{
  "id": "S001",
  "name": "Process name",
  "description": "Process purpose or description",
  "process_type": "C|R|D",
  "path": "quick|deep",
  "status": "partial|complete",
  "deep_dive_complete": false,
  "created": "YYYY-MM-DD",
  "updated": "YYYY-MM-DD",
  "owner_email": "user@example.com"
}
```

**Field Descriptions:**

- `id`: Unique assessment identifier (read-only)
- `name`: User-provided process name
- `description`: Process purpose or notes
- `process_type`: Type of process (C=Cross-functional, R=Repetitive, D=Decision-based)
- `path`: Assessment path (quick=quick look, deep=deep evaluation)
- `status`: Assessment completion status (partial, complete)
- `deep_dive_complete`: Whether deep-dive questions are completed (only relevant if path=deep)
- `created`: ISO date of assessment creation
- `updated`: ISO date of last update
- `owner_email`: Email of user who created the assessment

---

## Use Cases

### Retrieve All Assessments for a User

Use this to sync assessment data to an external system:

```bash
curl -b cookies.txt http://localhost:5000/api/v1/assessments | jq '.'
```

### Embed Assessment in External Tool

Fetch a specific assessment to display in an internal tool:

```bash
curl -b cookies.txt http://localhost:5000/api/v1/assessments/S001 | jq '.data'
```

### Build a Dashboard

Loop through assessments to build custom dashboards or reports:

```bash
curl -b cookies.txt http://localhost:5000/api/v1/assessments | \
  jq '.data[] | {id, name, status}'
```

---

## Rate Limiting

Currently, no rate limiting is enforced. Reasonable usage is expected.

---

## CORS

CORS is not currently enabled. API access is restricted to same-origin requests or authenticated sessions. To enable cross-origin API access from external domains, configure CORS on the Flask app.

---

## Future Enhancements

Potential API improvements:

- Create assessment endpoint (POST `/api/v1/assessments`)
- Update assessment endpoint (PUT `/api/v1/assessments/<id>`)
- Delete assessment endpoint (DELETE `/api/v1/assessments/<id>`)
- Filter by process type, status, date range
- Pagination for large result sets
- Token-based authentication (JWT) as alternative to session cookies
- Export to CSV/PDF formats
- Webhook subscriptions for assessment changes

---

## Support

For issues or questions about the API, contact the development team or check the application README.
