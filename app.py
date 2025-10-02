# app.py
import os
import json
from typing import Dict, Any, List, Optional

from flask import Flask, jsonify, request
from flask_cors import CORS

# --- Config ---
DATA_PATH = os.getenv("DATA_PATH", os.path.join("data", "ai_insights_sections.json"))

# Use overview by default
DEFAULT_SECTION = os.getenv("DEFAULT_SECTION", "overview_core")

# Friendly greeting to show in the UI when overview is returned
DEFAULT_GREETING = os.getenv(
    "DEFAULT_GREETING",
    "Hi Alisha, here’s an updated overview on all insights. Let me know if there’s anything else on your mind."
)

# OpenAI (LLM) — optional
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = None
if OPENAI_API_KEY:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
    except Exception:
        client = None

app = Flask(__name__)

# --- CORS (restrict to your frontends) ---
ALLOWED_ORIGINS = {
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    https://metric-quest-ai.vercel.app/,
}
CORS(
    app,
    resources={
        r"/": {"origins": list(ALLOWED_ORIGINS)},
        r"/metrics*": {"origins": list(ALLOWED_ORIGINS)},
        r"/ask": {"origins": list(ALLOWED_ORIGINS)},
        r"/healthz": {"origins": list(ALLOWED_ORIGINS)},
    },
    supports_credentials=False,
)

# --- Data Loading ---
def load_sections(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"DATA_PATH not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("Top-level JSON must be a list of section objects.")
        return data

def index_by_section(sections: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out = {}
    for entry in sections:
        name = str(entry.get("section", "")).strip()
        if name:
            out[name.lower()] = entry
    return out

SECTIONS_RAW = load_sections(DATA_PATH)
SECTIONS_BY_NAME = index_by_section(SECTIONS_RAW)
SECTION_KEYS = sorted(SECTIONS_BY_NAME.keys())

# --- Helpers ---
def normalize_key(raw: str) -> str:
    return raw.strip().lower().replace("-", "_").replace(" ", "_")

def format_payload(section_name: str) -> Dict[str, Any]:
    section_key = normalize_key(section_name)
    section = SECTIONS_BY_NAME.get(section_key)
    if not section:
        return {
            "ok": False,
            "error": f"Section '{section_name}' not found.",
            "available_sections": SECTION_KEYS,
        }
    return {
        "ok": True,
        "section": section.get("section"),
        "period": section.get("period"),
        "metrics": section.get("metrics", {}),
    }

def choose_section_with_llm(query: str, candidate_keys: List[str]) -> Optional[str]:
    if not client:
        return None
    brief_help = {
        "overview_core": "Overall KPIs across the program",
        "human_engagement_trends": "Human feedback, escalations, reviews",
        "agent_learning_progress": "Learning, models improving, training velocity",
        "operations_impact": "Ops metrics and efficiency",
        "roi_quarter": "Return on investment (quarterly)",
        "roi_annual": "Return on investment (annual)",
        "sales_impact": "Sales impact, revenue, pipeline, conversions",
    }
    candidates_text = "\n".join([f"- {k}: {brief_help.get(k, 'No desc')}" for k in candidate_keys])
    system = (
        "You select exactly one canonical section key from a fixed list. "
        "Return ONLY the key, nothing else. If unsure, pick the closest."
    )
    user = (
        f"User question/label: {query}\n\n"
        f"Choose one of these keys that best matches the user's intent:\n{candidates_text}\n\n"
        f"Return ONLY the key."
    )
    try:
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.0,
            max_tokens=10,
        )
        text = (resp.choices[0].message.content or "").strip().lower()
        text_norm = normalize_key(text)
        return text_norm if text_norm in candidate_keys else None
    except Exception:
        return None

# --- Routes ---
@app.route("/", methods=["GET"])
def root_default():
    """Default: greet and return overview metrics."""
    payload = format_payload(DEFAULT_SECTION)
    if payload.get("ok"):
        return jsonify({
            "message": DEFAULT_GREETING,
            "section": payload["section"],
            "period": payload["period"],
            "metrics": payload["metrics"],
        })
    return jsonify(payload), 404

@app.route("/metrics", methods=["GET"])
def get_metrics_query():
    """
    Fetch metrics:
      - Exact key:     /metrics?section=overview_core
      - Free-text LLM: /metrics?q=overview for the month
    """
    section = request.args.get("section")
    q = request.args.get("q")

    if section:
        payload = format_payload(section)
        return (jsonify(payload), 200) if payload.get("ok") else (jsonify(payload), 404)

    if q:
        chosen = choose_section_with_llm(q, SECTION_KEYS)
        if not chosen:
            return jsonify({
                "ok": False,
                "error": "Could not infer a section from your query.",
                "available_sections": SECTION_KEYS,
                "hint": "Try specifying ?section=<one of the keys>."
            }), 400
        payload = format_payload(chosen)
        # No verbose message — just return the data
        return jsonify(payload), 200

    return jsonify({
        "ok": False,
        "error": "Provide either ?section=<key> or ?q=<free-text>",
        "available_sections": SECTION_KEYS
    }), 400

@app.route("/metrics/<section_name>", methods=["GET"])
def get_metrics_path(section_name: str):
    payload = format_payload(section_name)
    return (jsonify(payload), 200) if payload.get("ok") else (jsonify(payload), 404)

@app.route("/ask", methods=["POST"])
def ask_llm_router():
    """
    POST { "q": "any free-text label or question" }
    Uses LLM to map to the best section and returns that section's metrics.
    """
    data = (request.get_json(silent=True) or {})
    q = str(data.get("q", "")).strip()
    if not q:
        return jsonify({"ok": False, "error": "Missing 'q' in JSON body."}), 400

    chosen = choose_section_with_llm(q, SECTION_KEYS)
    if not chosen:
        return jsonify({
            "ok": False,
            "error": "Could not infer a section from your query.",
            "available_sections": SECTION_KEYS
        }), 400

    payload = format_payload(chosen)

    # Add your friendly greeting only for overview
    msg = DEFAULT_GREETING if chosen == "overview_core" else None

    return jsonify({ **({"message": msg} if msg else {}), **payload }), 200

@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")), debug=True)
