# agent.py
from typing import Any, Dict, List
import json, re
from google.adk.agents import Agent

# -----------------------------
# TOOL 1: get_match_data
# -----------------------------
# 
def get_match_data(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    payload = {
      "match_json": { ... }        # dict OR
      "match_json_text": "..."     # JSON string
    }

    Minimal validation per assignment:
      - when:     "date"
      - who:      "home_team.name", "away_team.name"
      - result:   either "full_time_score" as "X:Y" OR integer pair
                  "home_team.score" and "away_team.score"
      - cards:    detected via events with types "yellow_card" or "red_card"
      - penalties: events "penalty_scored"/"penalty_missed" OR non-null "penalty_shootout"

    Returns:
      {"status":"success","facts": {...}}  or
      {"status":"error","error_message": "..."}
    """
    data = None
    if isinstance(payload.get("match_json"), dict):
        data = payload["match_json"]
    elif isinstance(payload.get("match_json_text"), str) and payload["match_json_text"].strip():
        try:
            data = json.loads(payload["match_json_text"])
        except Exception as e:
            return {"status": "error", "error_message": f"Invalid JSON: {e}"}
    else:
        return {"status": "error", "error_message": "Provide match_json (object) or match_json_text (JSON string)."}

    errors: List[str] = []

    # when
    date = data.get("date")
    if not isinstance(date, str) or not date.strip():
        errors.append("Missing or empty 'date'")

    # who
    home = data.get("home_team") or {}
    away = data.get("away_team") or {}
    home_name = home.get("name")
    away_name = away.get("name")
    if not isinstance(home_name, str) or not home_name.strip():
        errors.append("Missing 'home_team.name'")
    if not isinstance(away_name, str) or not away_name.strip():
        errors.append("Missing 'away_team.name'")

    # result
    ft = data.get("full_time_score")
    home_score = home.get("score")
    away_score = away.get("score")
    has_ft_string = isinstance(ft, str) and ":" in ft
    has_pair_scores = isinstance(home_score, int) and isinstance(away_score, int)
    if not (has_ft_string or has_pair_scores):
        errors.append("Provide either 'full_time_score' as 'X:Y' or integer 'home_team.score' and 'away_team.score'")

    # events (optional, but used for cards/penalties flags)
    events = data.get("events") or []
    if not isinstance(events, list):
        errors.append("'events' must be an array when present")

    if errors:
        return {
            "status": "error",
            "error_message": "There is an error in the provided JSON. " + "; ".join(errors) + ". I cannot proceed without it."
        }

    def _etype(ev: Dict[str, Any]) -> str:
        t = ev.get("type")
        return t if isinstance(t, str) else ""

    has_cards = any(_etype(e) in ("yellow_card", "red_card") for e in events)
    has_penalties_in_match = any(_etype(e) in ("penalty_scored", "penalty_missed") for e in events)
    has_penalty_shootout = data.get("penalty_shootout") is not None

    facts = {
        "date": date,
        "competition": data.get("competition"),
        "round": data.get("round"),
        "venue": data.get("venue"),
        "city": data.get("city"),
        "home_team": {"name": home_name, "score": home_score},
        "away_team": {"name": away_name, "score": away_score},
        "half_time_score": data.get("half_time_score"),
        "full_time_score": ft if has_ft_string else (f"{home_score}:{away_score}" if has_pair_scores else None),
        "penalty_shootout": data.get("penalty_shootout"),
        "events": events,
        "has_cards": has_cards,
        "has_penalties_in_match": has_penalties_in_match,
        "has_penalty_shootout": has_penalty_shootout,
    }
    return {"status": "success", "facts": facts}

# -----------------------------
# TOOL 2: word_count   
# ----------------------------
#

def word_count(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    payload = { "text": "...", "min_words": 260, "max_words": 340 }

    Returns:
      {"status":"ok"|"too_short"|"too_long", "count": int, "min": int, "max": int}
    """
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        return {"status": "error", "error_message": "empty text"}

    # Unicode-friendly tokenization
    tokens = re.findall(r"\b\w+\b", text, flags=re.UNICODE)
    count = len(tokens)

    min_w = int(payload.get("min_words", 260))
    max_w = int(payload.get("max_words", 340))
    if count < min_w:
        return {"status": "too_short", "count": count, "min": min_w, "max": max_w}
    if count > max_w:
        return {"status": "too_long", "count": count, "min": min_w, "max": max_w}
    return {"status": "ok", "count": count, "min": min_w, "max": max_w}

# -----------------------------
# TOOL 3: return_final — the only user-visible output
# -----------------------------
def return_final(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    payload = { "text": "final Czech article" }

    Echoes the final article. The instruction requires the model to NEVER
    output assistant text directly; it must call return_final at the end.
    ADK web will render the function response as the final answer.
    """
    text = payload.get("text", "")
    return {"text": text}

# -----------------------------
# AGENT
# -----------------------------
root_agent = Agent(
    name="match_report_agent",
    model="gemini-2.0-flash",
    description="Agent to generate ~300-word Czech football match reports.",
    instruction=(
        "You are a sports reporter.\n"
    "Your task is to write a football match recap in Czech that is STRICTLY between 260 and 340 words (target ≈ 300).\n"
    "\n"
    "PROCESS (mandatory):\n"
    "1) First, CALL the tool get_match_data with the provided match_json or match_json_text.\n"
    "   - If get_match_data returns status='error', immediately CALL return_final with the error_message and STOP.\n"
    "   - If status='success', use ONLY the returned facts object for everything that follows.\n"
    "\n"
    "2) Draft the article INTERNALLY (do NOT output it yet) under these rules:\n"
    "   - Keep ALL numbers, names, and minutes exactly as given; do NOT invent events, causes, set pieces, or extra details.\n"
    "   - Word-count goal is 300 (but the final can be 260–340).\n"
    "   - The article should contain at least information about when the match was played, who played, what the result was, whether any cards were given and whether penalties were taken.\n"
    "   - Structure the article as: lead → first half → second half → discipline/cards → penalties/shootout → closing.\n"
    "   - If ANY of the above requirements is not satisfied, the text is incorrect and must be revised before proceeding.\n"
    "\n"
    "3) CALL the tool word_count with payload {\"text\": draft, \"min_words\":260, \"max_words\":340}.\n"
    "   - If word_count.status == 'ok': CALL return_final with payload {\"text\": draft} and STOP.\n"
    "   - If word_count.status == 'too_short' or 'too_long': minimally revise the draft WITHOUT adding new facts, adjusting length by approximately 50–100 words in the needed direction, then CALL word_count again. Repeat until status == 'ok'.\n"
    "\n"
    "OUTPUT RULES:\n"
    "- NEVER output any assistant text directly at any time. Your ONLY user-visible output is via a single call to return_final.\n"
    "- Call return_final exactly once, only after word_count.status == 'ok'.\n"
    "- The final response must be a single plain-text article."
    ),
    tools=[get_match_data, word_count, return_final],
)

