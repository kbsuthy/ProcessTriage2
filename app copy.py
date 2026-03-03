from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any
import uuid

# -----------------------------
# Data model (in-memory)
# -----------------------------

@dataclass
class Assessment:
    id: str
    process_type: str
    name: str
    description: str
    answers: Dict[str, int]
    score: int  # 0–100
    recommendation: str
    suggestions: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))


# -----------------------------
# Dynamic question templates (your top 3)
# -----------------------------
# Keys:
#   C = Coordinating People / Requests Bouncing Around (verbal + mixed intake)
#   R = Reporting / Analytics Pulls
#   D = Data Entry / Copying Between Systems

PROCESS_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "C": {
        "label": "Coordinating People / Requests Bouncing Around",
        "questions": [
            {"key": "entry_points", "prompt": "How many different places can requests come in? (1=one place, 5=many places)", "weight": 4},
            {"key": "verbal_requests", "prompt": "How often do requests start verbally (meetings, hallway, calls)? (1=rare, 5=very often)", "weight": 4},
            {"key": "ownership", "prompt": "How unclear is ownership of the next step? (1=clear, 5=unclear)", "weight": 5},
            {"key": "intake_quality", "prompt": "How often do requests arrive missing key info? (1=rare, 5=often)", "weight": 4},
            {"key": "routing", "prompt": "How often is work routed to the wrong person first? (1=rare, 5=often)", "weight": 4},
            {"key": "visibility", "prompt": "How hard is it to see status without asking someone? (1=easy, 5=very hard)", "weight": 4},
            {"key": "handoffs", "prompt": "How many handoffs happen end-to-end? (1=few, 5=many)", "weight": 2},
            {"key": "waiting", "prompt": "How much time is spent waiting on others? (1=low, 5=high)", "weight": 3},
            {"key": "exceptions", "prompt": "How often do 'special cases' break the normal flow? (1=rare, 5=constant)", "weight": 2},
        ],
    },
    "R": {
        "label": "Reporting / Analytics Pulls",
        "questions": [
            {"key": "frequency", "prompt": "How often is this report needed? (1=rare, 5=very frequent)", "weight": 2},
            {"key": "sources", "prompt": "How many systems/sources feed the report? (1=few, 5=many)", "weight": 3},
            {"key": "manual_work", "prompt": "How manual is the process? (1=mostly automated, 5=all manual)", "weight": 4},
            {"key": "data_cleanliness", "prompt": "How messy/inconsistent is the input data? (1=clean, 5=messy)", "weight": 4},
            {"key": "definitions", "prompt": "How unclear are metric definitions (what counts)? (1=clear, 5=unclear)", "weight": 3},
            {"key": "stakeholders", "prompt": "How many people rely on it? (1=few, 5=many)", "weight": 2},
        ],
    },
    "D": {
        "label": "Data Entry / Copying Between Systems",
        "questions": [
            {"key": "frequency", "prompt": "How often do you do this? (1=rare, 5=daily)", "weight": 2},
            {"key": "repeat_steps", "prompt": "How repetitive are the steps? (1=unique, 5=very repetitive)", "weight": 4},
            {"key": "sources", "prompt": "How many systems/tools do you copy between? (1=1-2, 5=many)", "weight": 3},
            {"key": "errors", "prompt": "How error-prone is it? (1=low, 5=high)", "weight": 4},
            {"key": "time_cost", "prompt": "How time-consuming is it? (1=minutes, 5=hours+)", "weight": 3},
            {"key": "standardization", "prompt": "How inconsistent are inputs (formats, naming, missing fields)? (1=consistent, 5=inconsistent)", "weight": 3},
        ],
    },
}


# -----------------------------
# Recommendation buckets
# -----------------------------

def recommendation_from_percent(percent: float) -> str:
    if percent >= 80:
        return "Automate ASAP (high impact / high urgency)"
    if percent >= 60:
        return "Strong candidate for improvement (optimize or automate)"
    if percent >= 40:
        return "Document + streamline first (quick wins)"
    return "Low priority right now (monitor, revisit later)"


# -----------------------------
# Input helpers
# -----------------------------

def prompt_nonempty(message: str) -> str:
    while True:
        text = input(f"{message} ").strip()
        if text:
            return text
        print("Please enter something (can’t be blank).")

def prompt_int(message: str, min_val: int = 1, max_val: int = 5) -> int:
    while True:
        raw = input(f"{message} ").strip()
        try:
            val = int(raw)
            if min_val <= val <= max_val:
                return val
            print(f"Please enter a number between {min_val} and {max_val}.")
        except ValueError:
            print("Please enter a valid number.")

def choose_process_type() -> Optional[str]:
    print("\nChoose a process type:")
    for key, tpl in PROCESS_TEMPLATES.items():
        print(f"  {key}) {tpl['label']}")
    choice = input("\nEnter a letter (or press Enter to cancel): ").strip().upper()
    if choice == "":
        return None
    if choice in PROCESS_TEMPLATES:
        return choice
    print("Invalid choice.")
    return None


# -----------------------------
# Questionnaire engine
# -----------------------------

def run_questionnaire(template_key: str) -> Dict[str, int]:
    tpl = PROCESS_TEMPLATES[template_key]
    answers: Dict[str, int] = {}

    print(f"\n--- {tpl['label']} Questionnaire ---")
    print("Answer on a scale of 1–5.\n")

    for q in tpl["questions"]:
        answers[q["key"]] = prompt_int(q["prompt"], 1, 5)

    return answers

def score_answers(template_key: str, answers: Dict[str, int]) -> Dict[str, Any]:
    tpl = PROCESS_TEMPLATES[template_key]
    weighted_total = 0
    weighted_max = 0

    for q in tpl["questions"]:
        key = q["key"]
        weight = q.get("weight", 1)
        weighted_total += answers.get(key, 0) * weight
        weighted_max += 5 * weight

    percent = (weighted_total / weighted_max) * 100 if weighted_max else 0.0
    score_rounded = round(percent)  # 0–100
    rec = recommendation_from_percent(percent)

    return {"percent": percent, "score": score_rounded, "recommendation": rec}

def top_drivers(template_key: str, answers: Dict[str, int], n: int = 3) -> List[str]:
    tpl = PROCESS_TEMPLATES[template_key]
    impacts = []
    for q in tpl["questions"]:
        key = q["key"]
        weight = q.get("weight", 1)
        impact = answers.get(key, 0) * weight
        impacts.append((impact, q["prompt"]))
    impacts.sort(reverse=True, key=lambda x: x[0])
    return [prompt for _, prompt in impacts[:n]]


# -----------------------------
# Suggestions (rules engine)
# -----------------------------

def generate_suggestions(process_type: str, answers: Dict[str, int]) -> List[str]:
    suggestions: List[str] = []

    if process_type == "C":
        # Ownership problems = #1 cause of bouncing
        if answers.get("ownership", 0) >= 4:
            suggestions.append("Define next-step ownership rules: who owns a request from intake → completion so it stops bouncing around.")
            suggestions.append("Use a simple RACI or owner-of-the-moment model so there's always exactly one accountable person at any time.")

        # Multi-channel chaos (mix of everything)
        if answers.get("entry_points", 0) >= 4:
            suggestions.append("Consolidate intake: decide on one channel (form, inbox, or Teams message) and redirect all requests there.")
            suggestions.append("Add a quick auto-reply or pinned message that says, 'For requests, please use X' to stop random entry points.")

        # Verbal requests (meetings/hallways/calls)
        if answers.get("verbal_requests", 0) >= 4:
            suggestions.append("Create a 'meeting-to-intake' habit: before ending the meeting, put every request into the single intake channel.")
            suggestions.append("Use a quick capture tool (form/Teams message) during meetings so verbal requests don't vanish or multiply.")

        # Intake quality
        if answers.get("intake_quality", 0) >= 4:
            suggestions.append("Define a 'ready' checklist (required info) so requests stop triggering back-and-forth clarifications.")
            suggestions.append("Use a short intake form/template so requestors provide consistent information every time.")

        # Routing mistakes
        if answers.get("routing", 0) >= 4:
            suggestions.append("Create routing rules (categories → owner) so requests stop bouncing to the wrong person first.")

        # Status chasing
        if answers.get("visibility", 0) >= 4:
            suggestions.append("Create a visible queue/board with statuses (New → In Progress → Waiting → Done) to eliminate status-chasing DMs.")

        # Too many handoffs
        if answers.get("handoffs", 0) >= 4:
            suggestions.append("Reduce handoffs: assign a single coordinator or combine steps so fewer people touch each request.")

        # Waiting delays
        if answers.get("waiting", 0) >= 4:
            suggestions.append("Add light SLAs/response expectations and an escalation path for stuck requests.")

        # Too many exceptions
        if answers.get("exceptions", 0) >= 4:
            suggestions.append("Define an 'exception path' for special cases so they don’t derail the normal workflow.")

        # If hardly anything triggers, still help
        if len(suggestions) < 3:
            suggestions.append("Start by mapping the flow: steps + owners + statuses. Fix the single point where requests bounce the most.")

    elif process_type == "R":
        if answers.get("manual_work", 0) >= 4:
            suggestions.append("Automate data pulls (scheduled export/query) and standardize the transformation steps.")
        if answers.get("data_cleanliness", 0) >= 4:
            suggestions.append("Add validation rules (required fields, formatting checks) upstream to reduce cleanup time.")
        if answers.get("sources", 0) >= 4:
            suggestions.append("Consolidate sources or create a curated dataset so reports don’t stitch data every time.")
        if answers.get("definitions", 0) >= 4:
            suggestions.append("Write a metric dictionary (definitions + examples) to reduce disputes and rework.")
        if answers.get("frequency", 0) >= 4:
            suggestions.append("Turn it into a recurring automated report (scheduled delivery) instead of ad-hoc pulls.")
        if len(suggestions) < 2:
            suggestions.append("Document the report recipe (source → transform → output) and remove one manual step as a quick win.")

    elif process_type == "D":
        if answers.get("repeat_steps", 0) >= 4:
            suggestions.append("Use templates/forms to eliminate repetition and enforce consistent inputs.")
        if answers.get("sources", 0) >= 4:
            suggestions.append("Reduce tool-hopping: integrate systems or create a single intake that routes data.")
        if answers.get("errors", 0) >= 4:
            suggestions.append("Add guardrails: dropdowns, validation, and auto-fill to prevent common mistakes.")
        if answers.get("standardization", 0) >= 4:
            suggestions.append("Standardize naming + required fields to reduce exceptions and manual fixes.")
        if answers.get("time_cost", 0) >= 4:
            suggestions.append("Batch work + enable bulk import/export rather than one-at-a-time entry.")
        if len(suggestions) < 2:
            suggestions.append("Start with a template + validation checklist to reduce rework before attempting full automation.")

    # cap for readability
    return suggestions[:6]


# -----------------------------
# State utilities
# -----------------------------

def pick_assessment(state: Dict[str, Any]) -> Optional[str]:
    assessments: List[Assessment] = state["assessments"]
    if not assessments:
        print("\nNo assessments saved yet.\n")
        return None

    print("\nSaved assessments:")
    for i, a in enumerate(assessments, start=1):
        label = PROCESS_TEMPLATES.get(a.process_type, {}).get("label", "Unknown")
        print(f"  {i}. {a.name} [{label}] — {a.score}/100 — {a.created_at}")

    raw = input("\nEnter the number to select (or press Enter to cancel): ").strip()
    if raw == "":
        return None

    try:
        idx = int(raw)
        if 1 <= idx <= len(assessments):
            return assessments[idx - 1].id
    except ValueError:
        pass

    print("Invalid selection.")
    return None

def find_by_id(state: Dict[str, Any], assessment_id: str) -> Optional[Assessment]:
    for a in state["assessments"]:
        if a.id == assessment_id:
            return a
    return None


# -----------------------------
# Menu actions
# -----------------------------

def new_assessment(state: Dict[str, Any]) -> None:
    print("\n=== New Assessment ===")
    template_key = choose_process_type()
    if not template_key:
        print("\nCancelled.\n")
        return

    name = prompt_nonempty("\nProcess name:")
    description = prompt_nonempty("Short description:")

    answers = run_questionnaire(template_key)
    scoring = score_answers(template_key, answers)
    suggestions = generate_suggestions(template_key, answers)
    drivers = top_drivers(template_key, answers, n=3)

    assessment = Assessment(
        id=str(uuid.uuid4()),
        process_type=template_key,
        name=name,
        description=description,
        answers=answers,
        score=scoring["score"],
        recommendation=scoring["recommendation"],
        suggestions=suggestions,
    )
    state["assessments"].append(assessment)

    label = PROCESS_TEMPLATES[template_key]["label"]
    print("\n✅ Saved!")
    print(f"Type: {label}")
    print(f"Score: {assessment.score}/100")
    print(f"Recommendation: {assessment.recommendation}")

    print("\nTop drivers:")
    for d in drivers:
        print(f"  • {d}")

    print("\nTop suggestions:")
    for s in assessment.suggestions:
        print(f"  • {s}")
    print()

def list_assessments(state: Dict[str, Any]) -> None:
    print("\n=== All Assessments (In Memory) ===")
    assessments: List[Assessment] = state["assessments"]
    if not assessments:
        print("No assessments saved yet.\n")
        return

    for i, a in enumerate(assessments, start=1):
        label = PROCESS_TEMPLATES.get(a.process_type, {}).get("label", "Unknown")
        print(f"{i}. {a.name} [{label}] — {a.score}/100 — {a.recommendation} — {a.created_at}")
    print()

def view_assessment(state: Dict[str, Any]) -> None:
    print("\n=== View Assessment ===")
    assessment_id = pick_assessment(state)
    if not assessment_id:
        print()
        return

    a = find_by_id(state, assessment_id)
    if not a:
        print("Not found.\n")
        return

    label = PROCESS_TEMPLATES.get(a.process_type, {}).get("label", "Unknown")
    tpl = PROCESS_TEMPLATES.get(a.process_type, {})
    prompt_map = {q["key"]: q["prompt"] for q in tpl.get("questions", [])}

    print(f"\nName: {a.name}")
    print(f"Type: {label}")
    print(f"Created: {a.created_at}")
    print(f"\nDescription:\n  {a.description}")

    print("\nAnswers:")
    for key, val in a.answers.items():
        prompt = prompt_map.get(key, key)
        print(f"  • {prompt} -> {val}")

    print(f"\nScore: {a.score}/100")
    print(f"Recommendation: {a.recommendation}")

    if a.suggestions:
        print("\nTop suggestions:")
        for s in a.suggestions:
            print(f"  • {s}")

    drivers = top_drivers(a.process_type, a.answers, n=3)
    if drivers:
        print("\nTop drivers:")
        for d in drivers:
            print(f"  • {d}")

    print()

def delete_assessment(state: Dict[str, Any]) -> None:
    print("\n=== Delete Assessment ===")
    assessment_id = pick_assessment(state)
    if not assessment_id:
        print()
        return

    a = find_by_id(state, assessment_id)
    if not a:
        print("Not found.\n")
        return

    confirm = input(f"Type DELETE to confirm deleting '{a.name}': ").strip()
    if confirm == "DELETE":
        state["assessments"] = [x for x in state["assessments"] if x.id != assessment_id]
        print("🗑️ Deleted.\n")
    else:
        print("Cancelled.\n")

def help_screen(state: Dict[str, Any]) -> None:
    print("\n=== Help ===")
    print("This is an in-memory CLI prototype.")
    print("Assessments exist only while the program runs (nothing is saved to disk).")
    print("Process type determines the questions and suggestion rules (dynamic questionnaire).\n")

def quit_program(state: Dict[str, Any]) -> None:
    state["running"] = False
    print("\nGoodbye! (In-memory data will be lost.)\n")


# -----------------------------
# Dispatcher + main loop
# -----------------------------

def print_menu() -> None:
    print("=== Process Triage (Dynamic In-Memory Prototype) ===")
    print("1) New assessment")
    print("2) List assessments")
    print("3) View assessment")
    print("4) Delete assessment")
    print("H) Help")
    print("Q) Quit")

def main() -> None:
    state: Dict[str, Any] = {
        "running": True,
        "assessments": []
    }

    actions = {
        "1": new_assessment,
        "2": list_assessments,
        "3": view_assessment,
        "4": delete_assessment,
        "H": help_screen,
        "Q": quit_program
    }

    while state["running"]:
        print_menu()
        choice = input("\nChoose an option: ").strip().upper()
        action = actions.get(choice)

        if action:
            action(state)
        else:
            print("\nInvalid choice. Try again.\n")

if __name__ == "__main__":
    main()