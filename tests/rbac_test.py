"""
RBAC test suite — positive + negative cases for all 5 user profiles.
Run: python -m tests.rbac_test
"""

import json
import sys
import requests

# Force UTF-8 output so emoji in answers don't crash on Windows cp1252
sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://localhost:8000"
PASSWORD = "password"

# ── Test definitions ────────────────────────────────────────────────────────
# Each entry: (question, expect_blocked)
#   expect_blocked=False → role SHOULD get an answer (positive case)
#   expect_blocked=True  → role should be BLOCKED / denied (negative case)

TESTS = {
    "dr.mehta": {
        "role": "doctor",
        "cases": [
            # Positive — doctor can access clinical, nursing, general
            ("What is the treatment protocol for NSTEMI?",          False),
            ("What are the standard drug dosage guidelines?",       False),
            ("What are the ICU nursing procedures?",                False),
            ("What is the hospital leave policy?",                  False),
            # Negative — doctor cannot access billing or equipment
            ("What are the ICD-10 billing codes for cardiology?",   True),
            ("How do I calibrate the SterilPro 3000 autoclave?",    True),
        ],
    },
    "nurse.priya": {
        "role": "nurse",
        "cases": [
            # Positive — nurse can access nursing, general
            ("What are the ICU infection control guidelines?",      False),
            ("What is the hand hygiene protocol?",                  False),
            ("What is the hospital code of conduct?",               False),
            # Negative — nurse cannot access clinical, billing, equipment
            ("What is the drug formulary for antibiotics?",         True),
            ("What is the billing code for cashless claims?",       True),
            ("How do I troubleshoot the infusion pump?",            True),
        ],
    },
    "billing.ravi": {
        "role": "billing_executive",
        "cases": [
            # Positive — billing exec can access billing, general + SQL analytics on claims
            ("What are the insurance billing codes for pre-authorisation?", False),
            ("How many claims are currently pending?",              False),
            ("What is the total claimed amount for Bajaj Allianz?", False),
            # Negative — billing exec cannot access clinical, nursing, equipment
            ("What is the treatment protocol for cardiac arrest?",  True),
            ("What are the ICU nursing procedures?",                True),
            ("Which equipment category has the most open maintenance tickets?", True),
        ],
    },
    "tech.anand": {
        "role": "technician",
        "cases": [
            # Positive — technician can access equipment, general
            ("How do I calibrate the DriveFlow IP-200 infusion pump?", False),
            ("What is the preventive maintenance schedule?",        False),
            ("What is the staff leave policy?",                     False),
            # Negative — technician cannot access clinical, nursing, billing
            ("What is the antibiotic dosage for pneumonia?",        True),
            ("Show me the ICD-10 reimbursement billing guide.",     True),
            ("What is the ICU ventilator bundle protocol?",         True),
        ],
    },
    "admin.sys": {
        "role": "admin",
        "cases": [
            # Positive — admin can access everything
            ("What is the NSTEMI treatment protocol?",              False),
            ("What are the ICU nursing infection control steps?",   False),
            ("What are the ICD-10 billing codes?",                  False),
            ("How do I troubleshoot the SterilPro 3000?",           False),
            ("How many maintenance tickets are resolved?",          False),
            ("What is the hospital leave policy?",                  False),
        ],
    },
}

# ── Helpers ─────────────────────────────────────────────────────────────────

def login(username: str) -> str:
    r = requests.post(f"{BASE}/login", json={"username": username, "password": PASSWORD})
    r.raise_for_status()
    return r.json()["token"]


def ask(question: str, role: str, token: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.post(f"{BASE}/chat", json={"question": question, "role": role}, headers=headers, timeout=60)
    r.raise_for_status()
    return r.json()


def is_blocked(response: dict) -> bool:
    """
    Only match explicit RBAC denial messages — not general 'please contact'
    referrals that can appear in legitimate answers.
    """
    answer = response.get("answer", "").lower()
    block_phrases = [
        "do not have permission",
        "don't have access",
        "not authorised",
        "access denied",
        "not have access",
        "not authorized",
        "not permitted",
        "you are not authorised",
        "⚠️ **access denied",
        "can only query claims",
        "equipment maintenance records are managed by",
    ]
    return any(p in answer for p in block_phrases)


# ── Runner ───────────────────────────────────────────────────────────────────

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
WARN = "\033[93mWARN\033[0m"

def run():
    total = passed = failed = 0
    failures = []

    for username, cfg in TESTS.items():
        role = cfg["role"]
        print(f"\n{'='*60}")
        print(f"  User: {username}  |  Role: {role}")
        print(f"{'='*60}")

        try:
            token = login(username)
        except Exception as e:
            print(f"  [LOGIN ERROR] {e}")
            continue

        for question, expect_blocked in cfg["cases"]:
            total += 1
            label = "NEG (should block)" if expect_blocked else "POS (should answer)"
            try:
                resp = ask(question, role, token)
                blocked = is_blocked(resp)
                retrieval = resp.get("retrieval_type", "?")
                snippet = resp["answer"][:120].replace("\n", " ")

                if expect_blocked == blocked:
                    status = PASS
                    passed += 1
                else:
                    status = FAIL
                    failed += 1
                    failures.append((username, question, expect_blocked, blocked, snippet))

                print(f"  [{status}] [{label}]")
                print(f"         Q: {question}")
                print(f"         retrieval={retrieval}  blocked={blocked}")
                print(f"         A: {snippet}")
            except Exception as e:
                total += 1
                failed += 1
                status = FAIL
                print(f"  [{status}] [{label}]")
                print(f"         Q: {question}")
                print(f"         ERROR: {e}")
                failures.append((username, question, expect_blocked, None, str(e)))

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  RESULTS: {passed}/{total} passed  |  {failed} failed")
    print(f"{'='*60}")
    if failures:
        print("\n  FAILURES:")
        for u, q, exp, got, ans in failures:
            print(f"    [{u}] {q}")
            print(f"      Expected blocked={exp}, got blocked={got}")
            print(f"      Answer: {ans[:150]}")
    return failed


if __name__ == "__main__":
    sys.exit(run())
