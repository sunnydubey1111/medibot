import os
import sys
from pathlib import Path
from typing import List, Dict, Any, Tuple

# Ensure stdout uses UTF-8 to avoid encoding errors on Windows
sys.stdout.reconfigure(encoding='utf-8')

from qdrant_client import QdrantClient
from qdrant_client.http import models
from sentence_transformers import SentenceTransformer, CrossEncoder

# Import BM25Encoder from ingest
from backend.ingest import BM25Encoder


def _generate_hypothetical_answer(query: str) -> str:
    """
    HyDE: asks the LLM to write a hypothetical document excerpt that would answer
    the query, then uses that richer text for dense vector encoding.
    Falls back to the original query if the LLM is unavailable.
    """
    if not os.getenv("GEMINI_API_KEY"):
        return query
    try:
        from backend.llm_client import call_llm
        system = (
            "You are a medical document database. Given a clinical question, write a concise "
            "2-3 sentence excerpt from a relevant medical document that would directly answer it. "
            "Write in third person as if quoting the document. Do not use 'I'."
        )
        result = call_llm(query, system_instruction=system)
        if not result or any(x in result.lower() for x in ["oops!", "[mock", "trouble connecting"]):
            return query
        return result
    except Exception:
        return query

# Global variables to cache models
_dense_model = None
_cross_encoder = None
_bm25_encoder = None
_qdrant_client = None

BACKEND_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
BM25_PATH = BACKEND_DIR / "bm25_encoder.json"
QDRANT_DB_PATH = BACKEND_DIR / "qdrant_db"

def get_qdrant_client() -> QdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = QdrantClient(path=str(QDRANT_DB_PATH))
    return _qdrant_client

def get_dense_model() -> SentenceTransformer:
    global _dense_model
    if _dense_model is None:
        print("Loading SentenceTransformer model 'all-MiniLM-L6-v2' in retriever...")
        _dense_model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')
    return _dense_model

def get_cross_encoder() -> CrossEncoder:
    global _cross_encoder
    if _cross_encoder is None:
        print("Loading CrossEncoder model 'cross-encoder/ms-marco-MiniLM-L-6-v2'...")
        # Since it runs locally, it will download weights on first use
        _cross_encoder = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')
    return _cross_encoder

def get_bm25_encoder() -> BM25Encoder:
    global _bm25_encoder
    if _bm25_encoder is None:
        if not BM25_PATH.exists():
            raise FileNotFoundError(f"BM25 Encoder file not found at {BM25_PATH}. Please run ingestion first.")
        print("Loading BM25 Encoder...")
        _bm25_encoder = BM25Encoder.load(str(BM25_PATH))
    return _bm25_encoder


def retrieve_hybrid_and_rerank(query: str, user_role: str, top_k: int = 10, final_top_k: int = 3) -> List[Dict[str, Any]]:
    """
    Performs hybrid search in Qdrant (dense + sparse prefetch fused with RRF),
    applies role-based metadata filtering at retrieval time,
    and reranks the results using a cross-encoder.
    Returns a list of the top final_top_k scored chunks.
    """
    client = get_qdrant_client()
    dense_model = get_dense_model()
    cross_encoder = get_cross_encoder()
    bm25_encoder = get_bm25_encoder()
    
    # 1. HyDE: encode a hypothetical answer for richer dense coverage
    hyde_doc = _generate_hypothetical_answer(query)
    query_dense = dense_model.encode(hyde_doc).tolist()

    # 2. Sparse BM25 uses the original query for exact term matching
    sparse_indices, sparse_values = bm25_encoder.encode_query(query)
    
    # 3. Create the RBAC metadata filter
    # Matches points where the user's role is in the access_roles list
    rbac_filter = models.Filter(
        must=[
            models.FieldCondition(
                key="access_roles",
                match=models.MatchValue(value=user_role)
            )
        ]
    )
    
    # 4. Perform Qdrant hybrid query with prefetch and RRF
    # Using prefetch to query both vector indexes in parallel
    prefetch_dense = models.Prefetch(
        query=query_dense,
        using="dense",
        filter=rbac_filter,
        limit=top_k
    )
    
    prefetch_sparse = models.Prefetch(
        query=models.SparseVector(
            indices=sparse_indices,
            values=sparse_values
        ),
        using="sparse",
        filter=rbac_filter,
        limit=top_k
    )
    
    results = client.query_points(
        collection_name="medibot",
        prefetch=[prefetch_dense, prefetch_sparse],
        query=models.FusionQuery(
            fusion=models.Fusion.RRF
        ),
        limit=top_k,
        query_filter=rbac_filter
    )
    
    points = results.points
    if not points:
        return []
        
    # 5. Cross-Encoder Reranking
    # Feed query and retrieved chunk text together to score relevance
    # We use c.payload["embedded_text"] as the document representation
    pairs = [(query, p.payload["embedded_text"]) for p in points]
    scores = cross_encoder.predict(pairs)
    
    # Pair scores with points and sort descending
    scored_points = []
    for score, p in zip(scores, points):
        scored_points.append({
            "score": float(score),
            "text": p.payload["text"],
            "embedded_text": p.payload["embedded_text"],
            "source_document": p.payload["source_document"],
            "collection": p.payload["collection"],
            "access_roles": p.payload["access_roles"],
            "section_title": p.payload["section_title"],
            "chunk_type": p.payload["chunk_type"]
        })
        
    # Sort by cross-encoder score
    scored_points.sort(key=lambda x: x["score"], reverse=True)
    
    # Print scoring details for development logging
    print(f"\nReranking results for query: '{query}'")
    for idx, sp in enumerate(scored_points):
        print(f"  Rank {idx+1}: Doc={sp['source_document']} | Section={sp['section_title']} | Cross-Encoder Score={sp['score']:.4f}")
        
    return scored_points[:final_top_k]
