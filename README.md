Process Triage (Simplified)

A small CLI tool to score and capture lightweight process assessments. Each assessment is stored as a dictionary in a JSON list so results persist across sessions.

Quick start

1. Create and activate a virtual environment (macOS/Linux):

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies (none required):

```bash
pip install -r requirements.txt
```

3. Run the app:

```bash
python3 app.py
```

Persistence

- Assessments are saved to `data_store.json` in the project folder.
- The file is ignored by git via `.gitignore`.
- The app saves after each add/delete and on quit.

Files

- `app.py` — main CLI program.
- `data_store.json` — persistent store (auto-created).
- `.gitignore` — ignores `.venv`, caches, and `data_store.json`.
- `requirements.txt` — documents dependencies (none external).

Notes

- The CLI experience is unchanged from the prior prototype; however assessments are now stored as dictionaries in `data_store.json` for reuse across sessions.
