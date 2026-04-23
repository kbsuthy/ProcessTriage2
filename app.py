from datetime import datetime
from typing import Dict, List, Optional, Any
import uuid

from db import init_database, load_assessments, save_assessments

# -----------------------------
# Persistence utilities
# -----------------------------

init_database()

def load_data() -> List[Dict[str, Any]]:
    try:
        return load_assessments()
    except Exception:
        return []

def save_data(assessments: List[Dict[str, Any]]) -> None:
    try:
        save_assessments(assessments)
    except Exception:
        pass


# -----------------------------
# Universal question template for all processes
# -----------------------------

# Common questions for quick look (applies to all process types)
QUICK_LOOK_QUESTIONS = [
    {
        "key": "frequency",
        "text": "How frequently does this process happen?",
        "options": [
            ("rarely", "Rarely (a few times a year or less)"),
            ("occasionally", "Occasionally (monthly or quarterly)"),
            ("frequently", "Frequently (weekly)"),
            ("very_frequently", "Very frequently (daily or near-daily)")
        ],
        "explanation": "Frequency helps us understand how quickly small issues add up. Higher-frequency processes are usually stronger candidates for improvement because changes here can have a bigger cumulative impact."
    },
    {
        "key": "involvement",
        "text": "Who typically participates in this process?",
        "options": [
            ("one_person", "One person"),
            ("small_group", "A small group or team"),
            ("multiple_teams", "Multiple teams or departments"),
            ("external", "External partners or vendors")
        ],
        "multiple": True,
        "explanation": "The more people involved, the more opportunities there are for delays, miscommunication, or handoff issues. Processes involving multiple people or teams may benefit from clarification, standardization, or better tooling."
    },
    {
        "key": "frustration",
        "text": "Does this process involve frustration, delays, or workarounds?",
        "options": [
            ("no_issues", "No major issues"),
            ("minor", "Minor frustrations"),
            ("frequent", "Frequent frustration or delays"),
            ("painful", "Consistently painful or confusing")
        ],
        "explanation": "People usually feel process problems before they can describe them. Processes with higher frustration are often good candidates for closer review—even if they \"technically work.\""
    },
    {
        "key": "impact",
        "text": "If this process fails or is done incorrectly, what's the impact?",
        "options": [
            ("minor", "Minor inconvenience"),
            ("rework", "Rework or delays"),
            ("noticeable", "Noticeable impact on others"),
            ("high_risk", "High risk (compliance, safety, reputation, or major cost)")
        ],
        "explanation": "Not all processes carry the same risk. Even a small or infrequent process may deserve attention if the consequences of failure are high."
    },
    {
        "key": "consistency",
        "text": "Is this process done the same way every time?",
        "options": [
            ("very_consistent", "Very consistent"),
            ("mostly_consistent", "Mostly consistent with a few exceptions"),
            ("often_varies", "Often varies depending on the situation or person"),
            ("highly_variable", "Highly variable or unclear")
        ],
        "explanation": "Inconsistent processes are harder to train, harder to automate, and more likely to produce errors. High variability often signals a need for clearer guidance, better tools, or a more defined process."
    },
    {
        "key": "tools",
        "text": "How many tools or systems are typically used to complete this process?",
        "options": [
            ("one", "One tool"),
            ("few", "A few tools"),
            ("many", "Many tools"),
            ("manual", "Mostly manual (email, spreadsheets, copy/paste)")
        ],
        "explanation": "Process breakdowns often happen when information moves between tools or is handled manually. Processes with lots of tool switching or manual steps may have opportunities for simplification or automation."
    },
    {
        "key": "flagged",
        "text": "Has this process been discussed as an issue before?",
        "options": [
            ("no", "No"),
            ("informal", "Mentioned informally"),
            ("multiple", "Raised multiple times"),
            ("concern", "Actively causing concern")
        ],
        "explanation": "Recurring conversations about a process often signal unresolved issues. Known pain points are often high-value candidates for deeper analysis because people are already motivated for change."
    },
    {
        "key": "improvement_benefit",
        "text": "What would improving this process most likely improve?",
        "options": [
            ("time", "Time savings"),
            ("errors", "Fewer errors"),
            ("frustration", "Less frustration"),
            ("consistency", "Better consistency"),
            ("risk", "Reduced risk"),
            ("experience", "Better experience for others")
        ],
        "multiple": True,
        "explanation": "This helps us understand the potential value of improvement—not just the problem. Processes with clear improvement benefits are easier to prioritize and justify for deeper work."
    }
]

# Process type labels (kept for backward compatibility)
PROCESS_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "C": {
        "label": "Coordinating People / Requests Bouncing Around",
        "questions": QUICK_LOOK_QUESTIONS
    },
    "P": {
        "label": "Creation/Production",
        "questions": QUICK_LOOK_QUESTIONS
    },
    "R": {
        "label": "Reporting / Analytics Pulls",
        "questions": QUICK_LOOK_QUESTIONS
    },
    "D": {
        "label": "Data Entry / Copying Between Systems",
        "questions": QUICK_LOOK_QUESTIONS
    },
    "O": {
        "label": "Other",
        "questions": QUICK_LOOK_QUESTIONS
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

def score_answers(template_key: str, answers: Dict[str, Any]) -> Dict[str, Any]:
    """
    Score the new quick-look questions based on improvement priority.
    Answers are now string values (e.g., 'rarely', 'frequently') or lists for multi-select.
    Returns a dict with percent score and recommendation.
    """
    score = 0
    max_score = 0
    
    # Frequency scoring (weight: 15)
    freq = answers.get('frequency', '')
    if freq:
        max_score += 15
        freq_scores = {'rarely': 5, 'occasionally': 8, 'frequently': 12, 'very_frequently': 15}
        score += freq_scores.get(freq, 0)
    
    # Involvement scoring (weight: 15) - multi-select
    involvement = answers.get('involvement', [])
    if isinstance(involvement, str):
        involvement = [involvement]
    if involvement:
        max_score += 15
        involvement_score = len(involvement) * 4  # More people/teams = higher score
        score += min(involvement_score, 15)
    
    # Frustration scoring (weight: 20)
    frust = answers.get('frustration', '')
    if frust:
        max_score += 20
        frust_scores = {'no_issues': 0, 'minor': 7, 'frequent': 15, 'painful': 20}
        score += frust_scores.get(frust, 0)
    
    # Impact scoring (weight: 20)
    impact = answers.get('impact', '')
    if impact:
        max_score += 20
        impact_scores = {'minor': 5, 'rework': 10, 'noticeable': 15, 'high_risk': 20}
        score += impact_scores.get(impact, 0)
    
    # Consistency scoring (weight: 10)
    consistency = answers.get('consistency', '')
    if consistency:
        max_score += 10
        consistency_scores = {'very_consistent': 0, 'mostly_consistent': 3, 'often_varies': 7, 'highly_variable': 10}
        score += consistency_scores.get(consistency, 0)
    
    # Tools scoring (weight: 10)
    tools = answers.get('tools', '')
    if tools:
        max_score += 10
        tools_scores = {'one': 2, 'few': 5, 'many': 8, 'manual': 10}
        score += tools_scores.get(tools, 0)
    
    # Flagged scoring (weight: 10)
    flagged = answers.get('flagged', '')
    if flagged:
        max_score += 10
        flagged_scores = {'no': 0, 'informal': 3, 'multiple': 7, 'concern': 10}
        score += flagged_scores.get(flagged, 0)
    
    # Improvement benefit (multi-select) - bonus points
    benefits = answers.get('improvement_benefit', [])
    if isinstance(benefits, str):
        benefits = [benefits]
    if benefits:
        # More potential benefits = slightly higher priority
        benefit_bonus = min(len(benefits) * 2, 10)
        score += benefit_bonus
        max_score += 10
    
    # Calculate percentage
    percent = (score / max_score * 100) if max_score > 0 else 0
    
    # Generate recommendation
    if percent >= 70:
        recommendation = "High priority – Strong candidate for deeper evaluation"
    elif percent >= 50:
        recommendation = "Medium priority – Consider for improvement when resources allow"
    elif percent >= 30:
        recommendation = "Low-medium priority – Monitor and revisit periodically"
    else:
        recommendation = "Low priority – Process appears relatively stable"
    
    return {
        "percent": round(percent, 1),
        "score": round(percent),
        "recommendation": recommendation
    }

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
    assessments: List[Dict[str, Any]] = state["assessments"]
    if not assessments:
        print("\nNo assessments saved yet.\n")
        return None

    print("\nSaved assessments:")
    for i, a in enumerate(assessments, start=1):
        label = PROCESS_TEMPLATES.get(a.get("process_type"), {}).get("label", "Unknown")
        print(f"  {i}. {a.get('name')} [{label}] — {a.get('score')}/100 — {a.get('created_at')}")

    raw = input("\nEnter the number to select (or press Enter to cancel): ").strip()
    if raw == "":
        return None

    try:
        idx = int(raw)
        if 1 <= idx <= len(assessments):
            return assessments[idx - 1].get("id")
    except ValueError:
        pass

    print("Invalid selection.")
    return None

def find_by_id(state: Dict[str, Any], assessment_id: str) -> Optional[Dict[str, Any]]:
    for a in state["assessments"]:
        if a.get("id") == assessment_id:
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

    assessment = {
        "id": str(uuid.uuid4()),
        "process_type": template_key,
        "name": name,
        "description": description,
        "answers": answers,
        "score": scoring["score"],
        "recommendation": scoring["recommendation"],
        "suggestions": suggestions,
        "created_at": datetime.now().strftime("%Y-%m-%d"),
    }
    state["assessments"].append(assessment)
    # persist immediately so folder stays updated every session
    save_data(state["assessments"])

    label = PROCESS_TEMPLATES[template_key]["label"]
    print("\n✅ Saved!")
    print(f"Type: {label}")
    print(f"Score: {assessment.get('score')}/100")
    print(f"Recommendation: {assessment.get('recommendation')}")

    print("\nTop drivers:")
    for d in drivers:
        print(f"  • {d}")

    print("\nTop suggestions:")
    for s in assessment.get("suggestions", []):
        print(f"  • {s}")
    print()

def list_assessments(state: Dict[str, Any]) -> None:
    print("\n=== All Assessments ===")
    assessments: List[Dict[str, Any]] = state["assessments"]
    if not assessments:
        print("No assessments saved yet.\n")
        return

    for i, a in enumerate(assessments, start=1):
        label = PROCESS_TEMPLATES.get(a.get("process_type"), {}).get("label", "Unknown")
        print(f"{i}. {a.get('name')} [{label}] — {a.get('score')}/100 — {a.get('recommendation')} — {a.get('created_at')}")
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

    label = PROCESS_TEMPLATES.get(a.get("process_type"), {}).get("label", "Unknown")
    tpl = PROCESS_TEMPLATES.get(a.get("process_type"), {})
    prompt_map = {q["key"]: q["prompt"] for q in tpl.get("questions", [])}

    print(f"\nName: {a.get('name')}")
    print(f"Type: {label}")
    print(f"Created: {a.get('created_at')}")
    print(f"\nDescription:\n  {a.get('description')}")

    print("\nAnswers:")
    for key, val in a.get("answers", {}).items():
        prompt = prompt_map.get(key, key)
        print(f"  • {prompt} -> {val}")

    print(f"\nScore: {a.get('score')}/100")
    print(f"Recommendation: {a.get('recommendation')}")

    if a.get("suggestions"):
        print("\nTop suggestions:")
        for s in a.get("suggestions", []):
            print(f"  • {s}")

    drivers = top_drivers(a.get("process_type"), a.get("answers", {}), n=3)
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

    confirm = input(f"Type DELETE to confirm deleting '{a.get('name')}': ").strip()
    if confirm == "DELETE":
        state["assessments"] = [x for x in state["assessments"] if x.get("id") != assessment_id]
        # persist change
        save_data(state["assessments"])
        print("🗑️ Deleted.\n")
    else:
        print("Cancelled.\n")

def help_screen(state: Dict[str, Any]) -> None:
    print("\n=== Help ===")
    print("This CLI saves assessments to disk so they persist across sessions.")
    print("Process type determines the questions and suggestion rules (dynamic questionnaire).\n")

def quit_program(state: Dict[str, Any]) -> None:
    # ensure latest state is saved
    save_data(state.get("assessments", []))
    state["running"] = False
    print("\nGoodbye! Data saved to data_store.json.\n")


# -----------------------------
# Dispatcher + main loop
# -----------------------------

def print_menu() -> None:
    print("=== Process Triage ===")
    print("1) New assessment")
    print("2) List assessments")
    print("3) View assessment")
    print("4) Delete assessment")
    print("H) Help")
    print("Q) Quit")

def main() -> None:
    state: Dict[str, Any] = {
        "running": True,
        "assessments": load_data()
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