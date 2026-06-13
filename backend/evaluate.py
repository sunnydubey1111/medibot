"""
MediBot RAG Evaluation Script
Measures retrieval quality using LLM-based faithfulness and relevancy scoring.

Usage:
    python -m backend.evaluate
"""
import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

_backend_dir = Path(__file__).resolve().parent
load_dotenv(_backend_dir.parent / ".env")
sys.stdout.reconfigure(encoding="utf-8")

from backend.retriever import retrieve_hybrid_and_rerank
from backend.llm_client import call_llm

TEST_CASES = [
    {"role": "doctor",            "question": "What is the treatment protocol for NSTEMI?"},
    {"role": "nurse",             "question": "What are the ICU hand hygiene steps before inserting a central line?"},
    {"role": "technician",        "question": "What are the calibration steps for SterilPro 3000?"},
    {"role": "billing_executive", "question": "What documents are needed for submitting a cashless insurance claim?"},
]

_FAITHFULNESS_PROMPT = """\
Given the question, retrieved context, and generated answer, score how faithfully the answer
sticks to the retrieved context (0.0 = completely unfaithful, 1.0 = every claim is grounded).

Question: {question}
Context (truncated): {context}
Answer: {answer}

Respond with ONLY a decimal number between 0.0 and 1.0."""

_RELEVANCY_PROMPT = """\
Given the question and the generated answer, score how directly and completely the answer
addresses the question (0.0 = completely off-topic, 1.0 = fully answers the question).

Question: {question}
Answer: {answer}

Respond with ONLY a decimal number between 0.0 and 1.0."""


def _parse_score(raw: str):
    try:
        return round(float(raw.strip()), 3)
    except Exception:
        return None


def run_evaluation():
    results = []
    print("=== MediBot RAG Evaluation ===\n")

    for case in TEST_CASES:
        q, role = case["question"], case["role"]
        print(f"[{role}] {q}")

        try:
            chunks = retrieve_hybrid_and_rerank(q, role)
        except Exception as e:
            print(f"  Retrieval error: {e}\n")
            continue

        if not chunks:
            print("  No chunks retrieved.\n")
            continue

        context = "\n\n".join(c["embedded_text"] for c in chunks)
        top_score = chunks[0]["score"]

        answer = call_llm(
            prompt=f"Question: {q}\n\nContext:\n{context}\n\nAnswer concisely:",
            system_instruction="Answer using only the provided context.",
        )

        faithfulness = _parse_score(call_llm(
            _FAITHFULNESS_PROMPT.format(question=q, context=context[:2000], answer=answer)
        ))
        relevancy = _parse_score(call_llm(
            _RELEVANCY_PROMPT.format(question=q, answer=answer)
        ))

        result = {
            "role": role,
            "question": q,
            "chunks_retrieved": len(chunks),
            "top_source": chunks[0]["source_document"],
            "cross_encoder_top_score": round(top_score, 4),
            "faithfulness": faithfulness,
            "relevancy": relevancy,
        }
        results.append(result)
        print(f"  Sources      : {[c['source_document'] for c in chunks]}")
        print(f"  Rerank score : {top_score:.4f}")
        print(f"  Faithfulness : {faithfulness}  |  Relevancy: {relevancy}\n")

    valid = [r for r in results if r.get("faithfulness") is not None]
    if valid:
        avg_f = round(sum(r["faithfulness"] for r in valid) / len(valid), 3)
        avg_r = round(sum(r["relevancy"] for r in valid if r.get("relevancy") is not None) / len(valid), 3)
        print("=== Summary ===")
        print(f"Avg Faithfulness : {avg_f}")
        print(f"Avg Relevancy    : {avg_r}")

    report = _backend_dir / "eval_report.json"
    with open(report, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nReport saved → {report}")


if __name__ == "__main__":
    run_evaluation()
