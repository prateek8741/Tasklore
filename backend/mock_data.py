"""Sample responses used when no Gemini credentials are configured (MOCK mode).
Lets the frontend be built and tested before the Google Cloud setup is finished."""

MOCK_PLAYBOOK = {
    "title": "Reconcile vendor invoices in Excel",
    "goal": "Make sure every vendor invoice matches a purchase order before payment",
    "role_hint": "Accounts Executive",
    "tools": ["Excel", "Tally"],
    "skills": ["Invoice matching", "XLOOKUP", "GST input tax credit"],
    "estimated_minutes": 30,
    "steps": [
        {"title": "Export the ledger", "action": "In Tally, export the vendor ledger for the month to Excel",
         "why": "We always reconcile against the books, not the vendor's statement", "timestamp": "00:05"},
        {"title": "Match by invoice number", "action": "Use XLOOKUP to match invoice numbers against the PO sheet",
         "why": "Vendors sometimes send the same invoice twice", "timestamp": "00:22"},
        {"title": "Check GST numbers", "action": "Compare the GSTIN column with the vendor master",
         "why": "A wrong GSTIN means we lose the input tax credit", "timestamp": "00:41"},
    ],
    "gotchas": ["Forgetting to check GSTIN", "Matching on amount only, which hides duplicates"],
    "success_criteria": ["Every invoice is marked Matched or Flagged", "Flagged list is sent to the finance lead"],
    "narration_language": "English",
}


def mock_plan(role: str, days: int, library: dict) -> dict:
    """Same shape as schemas.PlanDraft; the API computes coverage and gaps from it."""
    pb_id = next(iter(library), "")
    return {
        "role": role[:80],
        "summary": "(MOCK) A gentle first week: tools and people first, then real tasks with practice.",
        "role_tasks": [
            {"task": "Reconcile vendor invoices against purchase orders", "importance": 3, "playbook_id": pb_id,
             "why_needed": "The role description mentions invoice reconciliation",
             "suggested_recorder": "Senior Accounts Executive", "needed_by_day": 2},
            {"task": "Raise a purchase order in Tally", "importance": 2, "playbook_id": "",
             "why_needed": "The role handles vendor payments", "suggested_recorder": "Procurement lead",
             "needed_by_day": 3},
            {"task": "Get approval for payments over ₹50,000", "importance": 2, "playbook_id": "",
             "why_needed": "New hires will hit this in week 1", "suggested_recorder": "Finance lead",
             "needed_by_day": 2},
        ],
        "items": [
            {"day": 1, "title": "Get access to Tally and the shared drive", "kind": "setup", "playbook_id": "",
             "detail": "Ask IT for Tally login and the Finance shared drive.", "stuck_example": "Tally says my user has no access to this company"},
            {"day": 1, "title": "Meet the finance lead", "kind": "meet", "playbook_id": "",
             "detail": "30 minutes on how month-end works and who approves what.", "stuck_example": "I don't know what to ask in the first meeting"},
            {"day": 2, "title": "Learn: reconcile vendor invoices", "kind": "learn", "playbook_id": pb_id,
             "detail": "Watch the recording and read the playbook steps.", "stuck_example": "XLOOKUP shows #N/A for every row"},
            {"day": 3, "title": "Practice: reconcile last month's invoices", "kind": "practice", "playbook_id": pb_id,
             "detail": "Do it yourself on a copy of last month's data.", "stuck_example": "My totals don't match the PO sheet"},
        ][: max(days, 1) * 2],
    }


MOCK_HINT = "(MOCK) Start by exporting just one week of data so it is easier to check. Then match on invoice number before amount."
