import os
import sys
import time
import jwt
import json
import asyncio
import bcrypt
import queue as q_module
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from typing import List, Dict, Any, Optional

sys.stdout.reconfigure(encoding='utf-8')

current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.dirname(current_dir)
load_dotenv(os.path.join(workspace_root, ".env"))

from backend.llm_client import call_llm, stream_llm
from backend.retriever import retrieve_hybrid_and_rerank
from backend.sql_rag import sql_rag_chain
from backend.audit_logger import log_query
from backend.conversation_store import get_history, save_turn

app = FastAPI(title="MediBot Backend", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Demo Users — bcrypt-hashed passwords (hashed once at startup) ---
print("Hashing demo passwords...")
def _h(pw: str) -> bytes:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=10))

DEMO_USERS = {
    "dr.mehta":     {"password_hash": _h("password"), "role": "doctor",            "name": "Dr. Mehta (Clinical)"},
    "nurse.priya":  {"password_hash": _h("password"), "role": "nurse",             "name": "Nurse Priya (Clinical)"},
    "billing.ravi": {"password_hash": _h("password"), "role": "billing_executive", "name": "Ravi (Billing)"},
    "tech.anand":   {"password_hash": _h("password"), "role": "technician",        "name": "Anand (Technician)"},
    "admin.sys":    {"password_hash": _h("password"), "role": "admin",             "name": "Admin System"},
}
print("Demo passwords hashed.")

ROLE_COLLECTIONS = {
    "doctor":            ["general", "clinical", "nursing"],
    "nurse":             ["general", "nursing"],
    "billing_executive": ["general", "billing"],
    "technician":        ["general", "equipment"],
    "admin":             ["general", "clinical", "nursing", "billing", "equipment"],
}

# --- JWT ---
JWT_SECRET = os.getenv("JWT_SECRET", "mediassist_secret_key_12345_secure_rag")

def encode_jwt(payload: dict) -> str:
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def decode_jwt(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])

# --- Per-user rate limiting (10 req / 60 s) ---
RATE_LIMIT_STORE: Dict[str, list] = {}
RATE_LIMIT_MAX = 10
RATE_LIMIT_WINDOW = 60

def check_rate_limit(username: str):
    now = time.time()
    ts = [t for t in RATE_LIMIT_STORE.get(username, []) if now - t < RATE_LIMIT_WINDOW]
    if len(ts) >= RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {RATE_LIMIT_MAX} requests per {RATE_LIMIT_WINDOW}s.",
        )
    ts.append(now)
    RATE_LIMIT_STORE[username] = ts

# --- Follow-up query expansion ---
LAST_QUERIES: Dict[str, str] = {}

def get_combined_query(question: str, username: str) -> str:
    q = question.lower().strip().rstrip("?.!")
    followups = [
        "elaborate", "elaborate more", "tell me more", "explain more", "more details",
        "more info", "details", "explain", "go on", "elaborate further", "further details",
        "substantial info", "give me more info", "what else", "tell me more about this",
        "elaborate more details", "can you elaborate", "can you explain",
        "elaborate more on this", "elaborate on this",
    ]
    if q in followups and username in LAST_QUERIES:
        prev = LAST_QUERIES[username]
        if prev:
            return f"{prev} (elaborate more details)"
    if len(q.split()) > 2:
        LAST_QUERIES[username] = question
    return question

# --- RBAC keyword pre-check ---
def check_restricted_keywords(question: str, role: str) -> Optional[str]:
    q = question.lower()
    cols = ROLE_COLLECTIONS.get(role, [])

    if "billing" not in cols and any(kw in q for kw in [
        "billing code", "icd-10", "icd10", "reimbursement", "pre-authorisation",
        "preauthorisation", "cashless", "claim", "insurer", "package rate",
        "billing guide", "billing doc",
    ]):
        return f"As a {role.replace('_', ' ')}, you do not have permission to access billing and insurance guides. Please contact the Billing Department or log in with the Billing Executive role."

    # Terms exclusively in clinical collection (block all non-clinical roles including nurses)
    _clinical_exclusive = [
        "dosage", "drug formulary", "treatment protocol", "coronary", "cardiac",
        "infarction", "nstemi", "diagnostics", "troponin", "prescribe",
        "treatment steps", "drug dosage", "clinical doc",
        "antibiotic", "medication", "drug", "pharma",
    ]
    # Terms that nursing collection also covers — only block roles with neither clinical nor nursing
    _clinical_nursing_overlap = [
        "medicine", "therapy", "diagnosis", "symptom", "disease",
        "infection", "patient care", "clinical",
    ]
    if "clinical" not in cols:
        if any(kw in q for kw in _clinical_exclusive):
            return f"As a {role.replace('_', ' ')}, you do not have permission to access clinical protocols and drug formularies. Please consult a doctor or log in with the Doctor role."
        if "nursing" not in cols and any(kw in q for kw in _clinical_nursing_overlap):
            return f"As a {role.replace('_', ' ')}, you do not have permission to access clinical or nursing documents. Please consult clinical staff."

    if "nursing" not in cols and any(kw in q for kw in [
        "nursing procedure", "icu guideline", "hand hygiene", "ventilator bundle",
        "patient fall", "patient monitoring", "icu protocol", "nursing doc",
    ]):
        return f"As a {role.replace('_', ' ')}, you do not have permission to access nursing procedures or ICU guidelines. Please contact nursing staff or log in with the Nurse role."

    if "equipment" not in cols and any(kw in q for kw in [
        "sterilpro", "driveflow", "autoclave", "infusion pump", "calibration",
        "maintenance checklist", "fault code", "troubleshoot", "equipment manual",
        "equipment doc",
    ]):
        return f"As a {role.replace('_', ' ')}, you do not have permission to access biomedical equipment manuals. Please contact Biomedical Engineering or log in with the Technician role."

    return None

# --- LLM system prompts ---
CLASSIFICATION_SYSTEM_INSTRUCTION = """
You are an intelligent query routing system for MediBot. Classify a healthcare staff member's query:
1. "sql_rag": analytical, statistics, counts, totals, claims, tickets, database records
2. "hybrid_rag": clinical protocols, drug details, procedures, policy guidelines, FAQs

Respond with ONLY one of: "sql_rag" or "hybrid_rag". No other text.
"""

RAG_ANSWER_SYSTEM_INSTRUCTION = """
You are MediBot, an intelligent clinical assistant for MediAssist Health Network.

FORMAT every response using this structure:
1. Start with one direct sentence answering the question.
2. Use ## Section Headers with an emoji (e.g. ## 💊 Dosage & Administration, ## ⚠️ Warnings, ## 📋 Steps).
3. Use bullet points (- item) for lists of criteria, symptoms, items, or options.
4. Use numbered lists (1. 2. 3.) for sequential procedures or steps.
5. **Bold** key terms, drug names, dosage values, and critical warnings.
6. End with a citation line: 📎 *Source: [document name] | [section title]*

EMOJI GUIDE — use contextually:
💊 medications/dosage | 🩺 diagnosis/clinical | ⚠️ critical warnings | 📋 policies/guidelines
💉 injections/IV/procedures | 🏥 facility/departments | ✅ approved actions | 📞 contact/referral
🔬 lab/diagnostics | 🩹 wound care | 🫀 cardiac | 🧬 pathology | ⚕️ general medical

CONTENT RULES:
- Cite source documents inline naturally (e.g. "According to **drug_formulary.pdf**...").
- If retrieved passages don't contain the answer, respond: "ℹ️ I couldn't find this in your authorised documents. Please contact [relevant department]."
- Never fabricate medical information. Be concise, accurate, and clinically safe.
"""

# --- Query router ---
def route_query(question: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY")

    def _kw():
        ql = question.lower()
        if any(k in ql for k in ["how many", "total claimed", "count", "claim", "ticket",
                                  "escalated", "average claim", "sum of", "insurer",
                                  "delete", "drop table", "logs"]):
            return "sql_rag"
        return "hybrid_rag"

    if not api_key:
        return _kw()
    try:
        raw = call_llm(
            f"Classify the following query:\nQuery: {question}",
            system_instruction=CLASSIFICATION_SYSTEM_INSTRUCTION,
        ).strip().lower()
        if any(x in raw for x in ["oops!", "trouble connecting", "spending cap", "exceeded"]):
            return _kw()
        return "sql_rag" if "sql" in raw else "hybrid_rag"
    except Exception:
        return _kw()

# --- Confidence helper ---
def _confidence(score: float):
    label = "high" if score > 0 else "medium" if score > -5 else "low"
    return round(score, 4), label

# --- Auth helper ---
def _extract_auth(user_role: str, authorization: Optional[str]):
    username = "anonymous"
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        try:
            payload = decode_jwt(token)
            user_role = payload.get("role", user_role).lower()
            username = payload.get("username", "anonymous")
            print(f"[JWT] '{username}' / '{user_role}'")
        except Exception as e:
            raise HTTPException(status_code=401, detail=f"Unauthorized: Invalid token ({e})")
    if user_role not in ROLE_COLLECTIONS:
        raise HTTPException(status_code=400, detail="Invalid user role")
    return username, user_role

# --- Pydantic models ---
class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    token: str
    refresh_token: str
    username: str
    role: str
    name: str

class RefreshRequest(BaseModel):
    refresh_token: str

class ChatRequest(BaseModel):
    question: str
    role: str

class ChatResponse(BaseModel):
    answer: str
    sources: List[Dict[str, Any]]
    retrieval_type: str
    role: str
    confidence_score: Optional[float] = None
    confidence_label: Optional[str] = None


# ─────────────────────────────────────────────
#  Endpoints
# ─────────────────────────────────────────────

@app.post("/login", response_model=LoginResponse)
def login(req: LoginRequest):
    user = DEMO_USERS.get(req.username.lower())
    if not user or not bcrypt.checkpw(req.password.encode(), user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    now = int(time.time())
    access = encode_jwt({
        "username": req.username.lower(), "role": user["role"],
        "name": user["name"], "type": "access", "exp": now + 3600,
    })
    refresh = encode_jwt({
        "username": req.username.lower(), "role": user["role"],
        "name": user["name"], "type": "refresh", "exp": now + 604800,
    })
    return LoginResponse(
        token=access, refresh_token=refresh,
        username=req.username.lower(), role=user["role"], name=user["name"],
    )


@app.post("/refresh")
def refresh_token(req: RefreshRequest):
    try:
        payload = decode_jwt(req.refresh_token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Refresh token has expired. Please log in again.")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid refresh token: {e}")
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid token type.")
    new_token = encode_jwt({
        "username": payload["username"], "role": payload["role"],
        "name": payload["name"], "type": "access", "exp": int(time.time()) + 3600,
    })
    return {"token": new_token}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, authorization: Optional[str] = Header(None)):
    user_role = req.role.lower()
    username, user_role = _extract_auth(user_role, authorization)
    check_rate_limit(username)

    original_question = req.question
    question = get_combined_query(original_question, username)

    restriction = check_restricted_keywords(question, user_role)
    if restriction:
        log_query(username, user_role, original_question, "blocked", blocked=True)
        return ChatResponse(answer=restriction, sources=[], retrieval_type="hybrid_rag", role=user_role)

    routed_type = route_query(question)
    print(f"\n[CHAT] '{original_question}' | {user_role} | {routed_type}")

    if routed_type == "sql_rag":
        if user_role not in ["billing_executive", "admin"]:
            msg = (f"As a {user_role.replace('_', ' ')}, I don't have access to database analytics. "
                   f"I can only search: {', '.join(ROLE_COLLECTIONS[user_role])}.")
            log_query(username, user_role, original_question, "sql_rag", blocked=True)
            return ChatResponse(answer=msg, sources=[], retrieval_type="sql_rag", role=user_role)
        # billing_executive can only query claims — block equipment/maintenance queries explicitly
        if user_role == "billing_executive":
            ql = question.lower()
            if any(k in ql for k in ["maintenance ticket", "equipment category", "equipment maintenance",
                                      "maintenance record", "open maintenance", "resolved maintenance"]):
                msg = ("As a billing executive, you can only query claims and billing data. "
                       "Equipment maintenance records are managed by the Biomedical Engineering department.")
                log_query(username, user_role, original_question, "sql_rag", blocked=True)
                return ChatResponse(answer=msg, sources=[], retrieval_type="sql_rag", role=user_role)
        answer = sql_rag_chain(question, user_role)
        log_query(username, user_role, original_question, "sql_rag")
        return ChatResponse(answer=answer, sources=[], retrieval_type="sql_rag", role=user_role)

    try:
        retrieved_chunks = retrieve_hybrid_and_rerank(question, user_role)
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=f"Search index not ready — run ingest.py first. ({e})")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Document search unavailable: {e}")

    if not retrieved_chunks:
        msg = f"I couldn't find relevant information in your permitted collections ({', '.join(ROLE_COLLECTIONS[user_role])})."
        return ChatResponse(answer=msg, sources=[], retrieval_type="hybrid_rag", role=user_role)

    sources = [{"source_document": c["source_document"], "section_title": c["section_title"],
                "collection": c["collection"]} for c in retrieved_chunks]
    confidence_score, confidence_label = _confidence(retrieved_chunks[0]["score"])

    context_str = "\n\n".join(
        f"--- Chunk {i+1} (Source: {c['source_document']} | Section: {c['section_title']} | Collection: {c['collection']}) ---\n{c['embedded_text']}"
        for i, c in enumerate(retrieved_chunks)
    )
    prompt_rag = f"User Question: {question}\n\nRetrieved Passages:\n{context_str}\n\nAnswer the question using the passages above:"

    try:
        answer = call_llm(prompt_rag, system_instruction=RAG_ANSWER_SYSTEM_INSTRUCTION, history=get_history(username))
    except Exception:
        answer = ""

    if not answer or any(x in answer.lower() for x in ["[mock", "oops!", "trouble connecting", "spending cap", "exceeded"]):
        relevant = [c for c in retrieved_chunks if c["score"] >= -10.5]
        if relevant:
            answer = "\n\n---\n\n".join(
                f"### From **{c['source_document']}** (Section: *{c['section_title']}*):\n\n{c['text'].strip()}"
                for c in relevant[:3]
            )
        else:
            answer = f"I couldn't find relevant information in your permitted collections ({', '.join(ROLE_COLLECTIONS[user_role])})."

    save_turn(username, question, answer)
    log_query(username, user_role, original_question, "hybrid_rag", confidence_score)

    return ChatResponse(
        answer=answer, sources=sources, retrieval_type="hybrid_rag", role=user_role,
        confidence_score=confidence_score, confidence_label=confidence_label,
    )


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest, authorization: Optional[str] = Header(None)):
    """Streaming variant of /chat — returns Server-Sent Events."""
    user_role = req.role.lower()
    username, user_role = _extract_auth(user_role, authorization)
    check_rate_limit(username)

    original_question = req.question
    question = get_combined_query(original_question, username)

    async def event_stream():
        loop = asyncio.get_running_loop()

        restriction = check_restricted_keywords(question, user_role)
        if restriction:
            log_query(username, user_role, original_question, "blocked", blocked=True)
            yield f"data: {json.dumps({'type': 'chunk', 'text': restriction})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'sources': [], 'retrieval_type': 'hybrid_rag', 'confidence_score': None, 'confidence_label': None})}\n\n"
            return

        routed_type = await loop.run_in_executor(None, route_query, question)
        print(f"\n[STREAM] '{original_question}' | {user_role} | {routed_type}")

        if routed_type == "sql_rag":
            if user_role not in ["billing_executive", "admin"]:
                msg = (f"As a {user_role.replace('_', ' ')}, I don't have access to database analytics. "
                       f"I can only search: {', '.join(ROLE_COLLECTIONS[user_role])}.")
                log_query(username, user_role, original_question, "sql_rag", blocked=True)
                yield f"data: {json.dumps({'type': 'chunk', 'text': msg})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'sources': [], 'retrieval_type': 'sql_rag', 'confidence_score': None, 'confidence_label': None})}\n\n"
                return
            if user_role == "billing_executive":
                ql = question.lower()
                if any(k in ql for k in ["maintenance ticket", "equipment category", "equipment maintenance",
                                          "maintenance record", "open maintenance", "resolved maintenance"]):
                    msg = ("As a billing executive, you can only query claims and billing data. "
                           "Equipment maintenance records are managed by the Biomedical Engineering department.")
                    log_query(username, user_role, original_question, "sql_rag", blocked=True)
                    yield f"data: {json.dumps({'type': 'chunk', 'text': msg})}\n\n"
                    yield f"data: {json.dumps({'type': 'done', 'sources': [], 'retrieval_type': 'sql_rag', 'confidence_score': None, 'confidence_label': None})}\n\n"
                    return
            answer = await loop.run_in_executor(None, sql_rag_chain, question)
            log_query(username, user_role, original_question, "sql_rag")
            yield f"data: {json.dumps({'type': 'chunk', 'text': answer})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'sources': [], 'retrieval_type': 'sql_rag', 'confidence_score': None, 'confidence_label': None})}\n\n"
            return

        try:
            retrieved_chunks = await loop.run_in_executor(None, retrieve_hybrid_and_rerank, question, user_role)
        except FileNotFoundError:
            yield f"data: {json.dumps({'type': 'error', 'text': 'Search index not ready. Please run backend/ingest.py first.'})}\n\n"
            return
        except Exception:
            yield f"data: {json.dumps({'type': 'error', 'text': 'Document search is currently unavailable.'})}\n\n"
            return

        if not retrieved_chunks:
            msg = f"I couldn't find relevant information in your permitted collections ({', '.join(ROLE_COLLECTIONS[user_role])})."
            yield f"data: {json.dumps({'type': 'chunk', 'text': msg})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'sources': [], 'retrieval_type': 'hybrid_rag', 'confidence_score': None, 'confidence_label': None})}\n\n"
            return

        sources = [{"source_document": c["source_document"], "section_title": c["section_title"],
                    "collection": c["collection"]} for c in retrieved_chunks]
        confidence_score, confidence_label = _confidence(retrieved_chunks[0]["score"])

        context_str = "\n\n".join(
            f"--- Chunk {i+1} (Source: {c['source_document']} | Section: {c['section_title']}) ---\n{c['embedded_text']}"
            for i, c in enumerate(retrieved_chunks)
        )
        prompt_rag = f"User Question: {question}\n\nRetrieved Passages:\n{context_str}\n\nAnswer the question using the passages above:"
        history = await loop.run_in_executor(None, get_history, username)

        # Stream LLM via a thread-safe queue so the async generator can yield chunks in real time
        chunk_queue = q_module.Queue()

        def _run_stream():
            try:
                for text in stream_llm(prompt_rag, RAG_ANSWER_SYSTEM_INSTRUCTION, history):
                    chunk_queue.put(text)
            except Exception:
                chunk_queue.put("Oops! I'm having trouble connecting to my system right now.")
            finally:
                chunk_queue.put(None)

        stream_future = loop.run_in_executor(None, _run_stream)
        full_answer = ""

        while True:
            text = await loop.run_in_executor(None, chunk_queue.get)
            if text is None:
                break
            full_answer += text
            yield f"data: {json.dumps({'type': 'chunk', 'text': text})}\n\n"

        await stream_future

        # Fallback if LLM failed
        if not full_answer or any(x in full_answer.lower() for x in ["[mock", "oops!", "trouble connecting"]):
            relevant = [c for c in retrieved_chunks if c.get("score", -99) >= -10.5]
            full_answer = "\n\n---\n\n".join(
                f"### From **{c['source_document']}** (Section: *{c['section_title']}*):\n\n{c['text'].strip()}"
                for c in relevant[:3]
            ) if relevant else "I couldn't find relevant information in your permitted collections."
            yield f"data: {json.dumps({'type': 'replace', 'text': full_answer})}\n\n"

        await loop.run_in_executor(None, save_turn, username, question, full_answer)
        log_query(username, user_role, original_question, "hybrid_rag", confidence_score)

        yield f"data: {json.dumps({'type': 'done', 'sources': sources, 'retrieval_type': 'hybrid_rag', 'confidence_score': confidence_score, 'confidence_label': confidence_label})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.get("/collections/{role}")
def get_collections(role: str):
    r = role.lower()
    if r not in ROLE_COLLECTIONS:
        raise HTTPException(status_code=400, detail="Invalid user role")
    return {"role": r, "collections": ROLE_COLLECTIONS[r]}


@app.get("/health")
def health():
    return {"status": "ok", "message": "MediBot backend is healthy"}
