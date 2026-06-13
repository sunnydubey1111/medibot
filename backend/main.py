import os
import sys
import time
import jwt
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from typing import List, Dict, Any, Optional

# Ensure stdout uses UTF-8 to avoid encoding errors on Windows
sys.stdout.reconfigure(encoding='utf-8')

# Load environment variables
current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.dirname(current_dir)
load_dotenv(os.path.join(workspace_root, ".env"))

# Import RAG and SQL pipelines
from backend.llm_client import call_llm
from backend.retriever import retrieve_hybrid_and_rerank
from backend.sql_rag import sql_rag_chain

app = FastAPI(title="MediBot Backend", version="1.0.0")

# Enable CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Demo User Credentials and Roles
DEMO_USERS = {
    "dr.mehta": {"password": "password", "role": "doctor", "name": "Dr. Mehta (Clinical)"},
    "nurse.priya": {"password": "password", "role": "nurse", "name": "Nurse Priya (Clinical)"},
    "billing.ravi": {"password": "password", "role": "billing_executive", "name": "Ravi (Billing)"},
    "tech.anand": {"password": "password", "role": "technician", "name": "Anand (Technician)"},
    "admin.sys": {"password": "password", "role": "admin", "name": "Admin System"}
}

ROLE_COLLECTIONS = {
    "doctor": ["general", "clinical", "nursing"],
    "nurse": ["general", "nursing"],
    "billing_executive": ["general", "billing"],
    "technician": ["general", "equipment"],
    "admin": ["general", "clinical", "nursing", "billing", "equipment"]
}

def check_restricted_keywords(question: str, role: str) -> Optional[str]:
    q = question.lower()
    allowed_cols = ROLE_COLLECTIONS.get(role, [])
    
    # 1. Billing keywords
    billing_keywords = ["billing code", "icd-10", "icd10", "reimbursement", "pre-authorisation", "preauthorisation", "cashless", "claim", "insurer", "package rate", "billing guide", "billing doc"]
    if "billing" not in allowed_cols:
        if any(kw in q for kw in billing_keywords):
            return f"As a {role.replace('_', ' ')}, you do not have permission to access billing and insurance guides. Please contact the Billing Department or log in with the Billing Executive role."
            
    # 2. Clinical keywords
    clinical_keywords = ["dosage", "drug formulary", "treatment protocol", "coronary", "cardiac", "infarction", "nstemi", "diagnostics", "troponin", "medicine", "prescribe", "treatment steps", "drug dosage", "clinical doc"]
    if "clinical" not in allowed_cols:
        if any(kw in q for kw in clinical_keywords):
            return f"As a {role.replace('_', ' ')}, you do not have permission to access clinical protocols and drug formularies. Please consult a doctor or log in with the Doctor role."
            
    # 3. Nursing keywords
    nursing_keywords = ["nursing procedure", "icu guideline", "hand hygiene", "ventilator bundle", "patient fall", "patient monitoring", "icu protocol", "nursing doc"]
    if "nursing" not in allowed_cols:
        if any(kw in q for kw in nursing_keywords):
            return f"As a {role.replace('_', ' ')}, you do not have permission to access nursing procedures or ICU guidelines. Please contact the nursing staff or log in with the Nurse role."
            
    # 4. Equipment keywords
    equipment_keywords = ["sterilpro", "driveflow", "autoclave", "infusion pump", "calibration", "maintenance checklist", "fault code", "troubleshoot", "equipment manual", "equipment doc"]
    if "equipment" not in allowed_cols:
        if any(kw in q for kw in equipment_keywords):
            return f"As a {role.replace('_', ' ')}, you do not have permission to access biomedical equipment manuals or maintenance logs. Please contact the Biomedical Engineering department or log in with the Technician role."
            
    return None

LAST_QUERIES = {}  # keyed by username to prevent cross-user context leakage

# Per-user rate limiting: max 10 requests per 60 seconds
RATE_LIMIT_STORE: Dict[str, list] = {}
RATE_LIMIT_MAX = 10
RATE_LIMIT_WINDOW = 60

def check_rate_limit(username: str):
    now = time.time()
    timestamps = RATE_LIMIT_STORE.get(username, [])
    timestamps = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]
    if len(timestamps) >= RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: maximum {RATE_LIMIT_MAX} requests per {RATE_LIMIT_WINDOW} seconds. Please wait before sending more messages."
        )
    timestamps.append(now)
    RATE_LIMIT_STORE[username] = timestamps

# Per-user conversation history for multi-turn context (capped at last 5 turns)
CONVERSATION_HISTORY: Dict[str, list] = {}
HISTORY_MAX_TURNS = 5

def get_user_history(username: str) -> list:
    return CONVERSATION_HISTORY.get(username, [])

def update_user_history(username: str, user_message: str, bot_answer: str):
    history = CONVERSATION_HISTORY.get(username, [])
    history.append({"role": "user", "parts": [user_message]})
    history.append({"role": "model", "parts": [bot_answer]})
    # Keep only the last N turns to avoid unbounded memory growth
    max_messages = HISTORY_MAX_TURNS * 2
    if len(history) > max_messages:
        history = history[-max_messages:]
    CONVERSATION_HISTORY[username] = history

def get_combined_query(question: str, username: str) -> str:
    q = question.lower().strip().rstrip("?.!")

    followup_keywords = [
        "elaborate", "elaborate more", "tell me more", "explain more", "more details",
        "more info", "details", "explain", "go on", "elaborate further", "further details",
        "substantial info", "give me more info", "what else", "tell me more about this",
        "elaborate more details", "can you elaborate", "can you explain", "elaborate more",
        "elaborate more on this", "elaborate on this"
    ]

    # If it's a follow-up and we have a previous query for this specific user
    if (q in followup_keywords or len(q.split()) <= 2) and username in LAST_QUERIES:
        prev_query = LAST_QUERIES[username]
        if prev_query:
            return f"{prev_query} (elaborate more details)"

    if len(q.split()) > 2:
        LAST_QUERIES[username] = question

    return question

CLASSIFICATION_SYSTEM_INSTRUCTION = """
You are an intelligent query routing system for MediBot. Your job is to classify a healthcare staff member's query into one of two categories:
1. "sql_rag": If the question is analytical, statistics-oriented, numbers-oriented, or refers to database records. Examples:
   - Counting claims, open tickets, tickets in a location, total claimed amounts, status of a ticket, resolved date, average claim size.
   - Any question about claims, insurers, patient names, departments billing numbers, equipment maintenance tickets, status, costs, counts, names in database tables.
2. "hybrid_rag": If the question asks for conceptual knowledge, clinical protocols, drug details, procedures, policy guidelines, handbooks, FAQs. Examples:
   - How to treat a patient, drug dosage, nursing calibration instructions, HR policies, leave application procedures, general FAQs.

Respond with ONLY one of these two strings: "sql_rag" or "hybrid_rag". Do not write any other text, markdown, or formatting.
"""

RAG_ANSWER_SYSTEM_INSTRUCTION = """
You are MediBot, an intelligent clinical assistant for MediAssist Health Network.
You are given the user's question and the top retrieved passages from the authorized document collections.
Your job is to provide a comprehensive, accurate, and helpful response.
Cite the source documents and section headings clearly in your text (e.g. "According to the HR Leave Policy...").
If the provided passages do not contain the answer, state that you cannot find the answer in the authorized documents, but suggest who or which department they can contact.
Maintain a professional, helpful, and clinically safe tone.
"""

JWT_SECRET = os.getenv("JWT_SECRET", "mediassist_secret_key_12345_secure_rag")

def encode_jwt(payload: dict) -> str:
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def decode_jwt(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])

class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    token: str
    username: str
    role: str
    name: str

class ChatRequest(BaseModel):
    question: str
    role: str

class ChatResponse(BaseModel):
    answer: str
    sources: List[Dict[str, Any]]
    retrieval_type: str
    role: str

def route_query(question: str) -> str:
    """
    Classifies the incoming question into either 'sql_rag' or 'hybrid_rag'.
    Uses LLM if key is set, falls back to keyword matching otherwise.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    
    def keyword_fallback():
        q_lower = question.lower()
        sql_keywords = [
            "how many", "total", "amount", "count", "claim", "ticket", 
            "escalated", "approved", "status of", "average", "sum", 
            "raised", "resolved", "pending", "insurer", "delete", "drop table", "logs"
        ]
        if any(kw in q_lower for kw in sql_keywords):
            return "sql_rag"
        return "hybrid_rag"
        
    if not api_key:
        return keyword_fallback()
        
    try:
        raw_class = call_llm(
            prompt=f"Classify the following query:\nQuery: {question}",
            system_instruction=CLASSIFICATION_SYSTEM_INSTRUCTION
        ).strip().lower()
        
        if "oops!" in raw_class or "trouble connecting" in raw_class or "spending cap" in raw_class or "exceeded" in raw_class:
            return keyword_fallback()
            
        if "sql" in raw_class:
            return "sql_rag"
        return "hybrid_rag"
    except Exception:
        return keyword_fallback()


@app.post("/login", response_model=LoginResponse)
def login(req: LoginRequest):
    user = DEMO_USERS.get(req.username.lower())
    if not user or user["password"] != req.password:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    # Generate real JWT token containing user metadata
    payload = {
        "username": req.username.lower(),
        "role": user["role"],
        "name": user["name"],
        "exp": int(time.time()) + 86400  # Token valid for 24 hours
    }
    token = encode_jwt(payload)
    
    return LoginResponse(
        token=token,
        username=req.username.lower(),
        role=user["role"],
        name=user["name"]
    )


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, authorization: Optional[str] = Header(None)):
    user_role = req.role.lower()
    
    # Extract and verify JWT token from Authorization header if present
    username = "anonymous"
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        try:
            payload = decode_jwt(token)
            user_role = payload.get("role", user_role).lower()
            username = payload.get("username", "anonymous")
            print(f"[JWT Auth] Verified token for user '{username}' with role '{user_role}'")
        except Exception as e:
            raise HTTPException(status_code=401, detail=f"Unauthorized: Invalid token ({str(e)})")
    if user_role not in ROLE_COLLECTIONS:
        raise HTTPException(status_code=400, detail="Invalid user role")

    check_rate_limit(username)

    original_question = req.question
    question = get_combined_query(original_question, username)
    
    # 0. Check for role-based restricted keyword queries first
    restriction_message = check_restricted_keywords(question, user_role)
    if restriction_message:
        return ChatResponse(
            answer=restriction_message,
            sources=[],
            retrieval_type="hybrid_rag",
            role=user_role
        )
    
    # 1. Route the query (SQL vs Document RAG)
    routed_type = route_query(question)
    print(f"\n[CHAT API] Question: '{original_question}' (Combined: '{question}') | Role: '{user_role}' | Routed Type: '{routed_type}'")
    
    # 2. Process SQL RAG
    if routed_type == "sql_rag":
        # RBAC Check: SQL RAG is only for billing_executive and admin
        if user_role not in ["billing_executive", "admin"]:
            # Format custom RBAC rejection response
            refusal_message = f"As a {user_role.replace('_', ' ')}, I don't have access to search database analytics or claims statistics. I can only search guide documents and policies authorized for your role, such as {', '.join(ROLE_COLLECTIONS[user_role])}."
            return ChatResponse(
                answer=refusal_message,
                sources=[],
                retrieval_type="sql_rag",
                role=user_role
            )
            
        # Execute SQL RAG chain
        answer = sql_rag_chain(question)
        return ChatResponse(
            answer=answer,
            sources=[],
            retrieval_type="sql_rag",
            role=user_role
        )
        
    # 3. Process Hybrid Document RAG
    else:
        # Retrieve top chunks with retrieval-layer RBAC filtering
        try:
            retrieved_chunks = retrieve_hybrid_and_rerank(question, user_role)
        except FileNotFoundError as e:
            raise HTTPException(status_code=503, detail=f"Search index not ready. Please run ingestion first. ({str(e)})")
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Document search unavailable: {str(e)}")

        # If no chunks returned, inform the user they don't have access or no matches
        if not retrieved_chunks:
            refusal_message = f"I couldn't find any relevant information in the guides and policies you have permission to view ({', '.join(ROLE_COLLECTIONS[user_role])}). If you require clinical protocols, billing guides, or equipment manuals, please log in with the appropriate role."
            return ChatResponse(
                answer=refusal_message,
                sources=[],
                retrieval_type="hybrid_rag",
                role=user_role
            )
            
        # Format sources to return
        sources = []
        for chunk in retrieved_chunks:
            sources.append({
                "source_document": chunk["source_document"],
                "section_title": chunk["section_title"],
                "collection": chunk["collection"]
            })
            
        # Combine chunks text as context
        context_blocks = []
        for idx, chunk in enumerate(retrieved_chunks):
            context_blocks.append(f"--- Chunk {idx+1} (Source: {chunk['source_document']} | Section: {chunk['section_title']} | Collection: {chunk['collection']}) ---\n{chunk['embedded_text']}")
            
        context_str = "\n\n".join(context_blocks)
        
        # Call LLM to format natural language answer
        prompt_rag = f"""
        User Question: {question}
        
        Retrieved Passages:
        {context_str}
        
        Answer the question using the passages above:
        """
        
        try:
            answer = call_llm(
                prompt=prompt_rag,
                system_instruction=RAG_ANSWER_SYSTEM_INSTRUCTION,
                history=get_user_history(username)
            )
        except Exception:
            answer = ""
            
        if not answer or "[mock" in answer.lower() or "oops!" in answer.lower() or "trouble connecting" in answer.lower() or "spending cap" in answer.lower() or "exceeded" in answer.lower():
            # Check if the retrieved chunk is actually relevant (score >= -10.5)
            top_chunk = retrieved_chunks[0]
            if top_chunk["score"] < -10.5:
                answer = f"I couldn't find any relevant information in the guides and policies you have permission to view ({', '.join(ROLE_COLLECTIONS[user_role])}). If you require clinical protocols, billing guides, or equipment manuals, please log in with the appropriate role."
            else:
                # Compile substantial, complete information from all relevant retrieved chunks
                relevant_chunks = [c for c in retrieved_chunks if c["score"] >= -10.5]
                answer_parts = []
                for idx, chunk in enumerate(relevant_chunks[:3]): # Use up to top 3 relevant chunks
                    doc = chunk["source_document"]
                    sec = chunk["section_title"]
                    text = chunk["text"].strip()
                    answer_parts.append(f"### From **{doc}** (Section: *{sec}*):\n\n{text}")
                
                answer = "\n\n---\n\n".join(answer_parts)
            
        update_user_history(username, question, answer)
        return ChatResponse(
            answer=answer,
            sources=sources,
            retrieval_type="hybrid_rag",
            role=user_role
        )


@app.get("/collections/{role}")
def get_collections(role: str):
    role_lower = role.lower()
    if role_lower not in ROLE_COLLECTIONS:
        raise HTTPException(status_code=400, detail="Invalid user role")
    return {
        "role": role_lower,
        "collections": ROLE_COLLECTIONS[role_lower]
    }


@app.get("/health")
def health():
    return {"status": "ok", "message": "MediBot backend is healthy"}
