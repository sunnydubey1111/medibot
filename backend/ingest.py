import os
import sys
import json
import math
import re
from pathlib import Path
from collections import Counter
from typing import List, Dict, Tuple, Any

# Ensure stdout uses UTF-8 to avoid encoding errors on Windows
sys.stdout.reconfigure(encoding='utf-8')

# Docling imports
from docling.document_converter import DocumentConverter
from docling.chunking import HierarchicalChunker

# Sentence Transformers for dense embeddings
from sentence_transformers import SentenceTransformer

# Qdrant client
from qdrant_client import QdrantClient
from qdrant_client.http import models

# Define the collections mapping
COLLECTIONS_INFO = {
    "general": {
        "roles": ["doctor", "nurse", "billing_executive", "technician", "admin"],
        "folder": "general"
    },
    "clinical": {
        "roles": ["doctor", "admin"],
        "folder": "clinical"
    },
    "nursing": {
        "roles": ["nurse", "doctor", "admin"],
        "folder": "nursing"
    },
    "billing": {
        "roles": ["billing_executive", "admin"],
        "folder": "billing"
    },
    "equipment": {
        "roles": ["technician", "admin"],
        "folder": "equipment"
    }
}

class BM25Encoder:
    def __init__(self, b=0.75, k1=1.5):
        self.b = b
        self.k1 = k1
        self.doc_count = 0
        self.vocab = {}  # word -> index
        self.idf = {}    # index -> idf value
        self.doc_lens = []
        self.avg_doc_len = 0.0
        self.doc_freqs = Counter()  # index -> doc count

    def tokenize(self, text: str) -> List[str]:
        return re.findall(r'\b\w{2,}\b', text.lower())

    def fit(self, corpus_texts: List[str]):
        tokenized_corpus = []
        words_set = set()
        self.doc_lens = []
        
        for text in corpus_texts:
            tokens = self.tokenize(text)
            tokenized_corpus.append(tokens)
            words_set.update(tokens)
            self.doc_lens.append(len(tokens))
            
        self.vocab = {word: idx for idx, word in enumerate(sorted(words_set))}
        self.doc_count = len(corpus_texts)
        self.avg_doc_len = sum(self.doc_lens) / max(self.doc_count, 1)
        
        # Count document frequencies
        for tokens in tokenized_corpus:
            unique_indices = set(self.vocab[token] for token in tokens if token in self.vocab)
            for idx in unique_indices:
                self.doc_freqs[idx] += 1
                
        # Calculate IDF
        for idx in range(len(self.vocab)):
            df = self.doc_freqs[idx]
            self.idf[idx] = math.log((self.doc_count - df + 0.5) / (df + 0.5) + 1.0)
            
    def encode(self, text: str) -> Tuple[List[int], List[float]]:
        tokens = self.tokenize(text)
        if not tokens:
            return [], []
            
        token_counts = Counter(tokens)
        doc_len = len(tokens)
        
        indices = []
        values = []
        
        for token, count in token_counts.items():
            if token not in self.vocab:
                continue
            idx = self.vocab[token]
            idf_val = self.idf[idx]
            
            tf = (count * (self.k1 + 1)) / (count + self.k1 * (1 - self.b + self.b * (doc_len / self.avg_doc_len)))
            score = idf_val * tf
            
            indices.append(idx)
            values.append(float(score))
            
        if not indices:
            return [], []
            
        sorted_pairs = sorted(zip(indices, values))
        indices, values = zip(*sorted_pairs)
        return list(indices), list(values)

    def encode_query(self, query: str) -> Tuple[List[int], List[float]]:
        tokens = self.tokenize(query)
        if not tokens:
            return [], []
            
        token_counts = Counter(tokens)
        indices = []
        values = []
        
        for token, count in token_counts.items():
            if token not in self.vocab:
                continue
            idx = self.vocab[token]
            idf_val = self.idf[idx]
            indices.append(idx)
            values.append(float(idf_val * count))
            
        if not indices:
            return [], []
            
        sorted_pairs = sorted(zip(indices, values))
        indices, values = zip(*sorted_pairs)
        return list(indices), list(values)

    def save(self, filepath: str):
        data = {
            "b": self.b,
            "k1": self.k1,
            "doc_count": self.doc_count,
            "vocab": self.vocab,
            "idf": {str(k): v for k, v in self.idf.items()},
            "avg_doc_len": self.avg_doc_len,
            "doc_freqs": {str(k): v for k, v in self.doc_freqs.items()}
        }
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, filepath: str):
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        encoder = cls(b=data["b"], k1=data["k1"])
        encoder.doc_count = data["doc_count"]
        encoder.vocab = data["vocab"]
        encoder.idf = {int(k): v for k, v in data["idf"].items()}
        encoder.avg_doc_len = data["avg_doc_len"]
        encoder.doc_freqs = Counter({int(k): v for k, v in data["doc_freqs"].items()})
        return encoder


def get_chunk_type(doc_items: List[Any]) -> str:
    # Determine type of the chunk based on items
    types = set()
    for item in doc_items:
        label = getattr(item, 'label', '').lower()
        if 'table' in label:
            types.add('table')
        elif 'code' in label:
            types.add('code')
        elif 'heading' in label or 'header' in label:
            types.add('heading')
        else:
            types.add('text')
            
    if 'table' in types:
        return 'table'
    if 'code' in types:
        return 'code'
    if 'heading' in types:
        return 'heading'
    return 'text'


def parse_and_chunk_documents(data_root: Path) -> List[Dict[str, Any]]:
    converter = DocumentConverter()
    chunker = HierarchicalChunker()
    all_chunks = []

    for col_name, info in COLLECTIONS_INFO.items():
        col_dir = data_root / info["folder"]
        if not col_dir.exists():
            print(f"Directory {col_dir} does not exist, skipping.")
            continue
            
        print(f"Processing collection '{col_name}' from folder: {col_dir}")
        for file_path in col_dir.glob("*"):
            if file_path.suffix.lower() not in ['.pdf', '.md']:
                continue
                
            print(f"  Parsing file: {file_path.name}")
            try:
                result = converter.convert(file_path)
                doc = result.document
                chunks = list(chunker.chunk(doc))
                print(f"    Generated {len(chunks)} chunks.")
                
                for idx, chunk in enumerate(chunks):
                    # Carry parent section heading as context in the text
                    headings = getattr(chunk.meta, "headings", [])
                    section_title = headings[-1] if headings else ""
                    
                    heading_context = " > ".join(headings)
                    if heading_context:
                        embedded_text = f"Context: {heading_context}\n\n{chunk.text}"
                    else:
                        embedded_text = chunk.text
                        
                    doc_items = getattr(chunk.meta, "doc_items", [])
                    chunk_type = get_chunk_type(doc_items)
                    
                    all_chunks.append({
                        "id": f"{file_path.name}_{idx}",
                        "raw_text": chunk.text,
                        "embedded_text": embedded_text,
                        "metadata": {
                            "source_document": file_path.name,
                            "collection": col_name,
                            "access_roles": info["roles"],
                            "section_title": section_title,
                            "chunk_type": chunk_type
                        }
                    })
            except Exception as e:
                print(f"    Error parsing {file_path.name}: {e}")
                
    return all_chunks


def main():
    backend_dir = Path(os.path.dirname(os.path.abspath(__file__)))
    data_root = backend_dir.parent / "docs" / "mediassist_data" / "mediassist_data"
    backend_dir.mkdir(exist_ok=True)
    
    # 1. Parse and chunk all files
    print("Starting document parsing and chunking...")
    chunks = parse_and_chunk_documents(data_root)
    print(f"Completed parsing. Total chunks generated: {len(chunks)}")
    
    if not chunks:
        print("No chunks generated. Ingestion aborted.")
        return

    # 2. Fit BM25 Encoder on embedded texts
    print("Fitting BM25 Encoder...")
    texts_to_fit = [c["embedded_text"] for c in chunks]
    bm25_encoder = BM25Encoder()
    bm25_encoder.fit(texts_to_fit)
    
    bm25_path = backend_dir / "bm25_encoder.json"
    bm25_encoder.save(str(bm25_path))
    print(f"BM25 Encoder fitted and saved to {bm25_path}")

    # 3. Generate Dense Embeddings
    print("Loading SentenceTransformer model 'all-MiniLM-L6-v2'...")
    dense_model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')
    
    print("Generating dense embeddings...")
    embedded_texts = [c["embedded_text"] for c in chunks]
    dense_embeddings = dense_model.encode(embedded_texts, show_progress_bar=True)
    print("Dense embeddings generated successfully.")

    # 4. Set up Qdrant Database
    qdrant_db_path = backend_dir / "qdrant_db"
    print(f"Initializing local SQLite-backed Qdrant client at {qdrant_db_path}...")
    qdrant_client = QdrantClient(path=str(qdrant_db_path))
    
    collection_name = "medibot"
    
    # Recreate collection
    if qdrant_client.collection_exists(collection_name):
        print(f"Collection '{collection_name}' already exists. Recreating...")
        qdrant_client.delete_collection(collection_name)
        
    print(f"Creating collection '{collection_name}' with dense (384) and sparse vector configurations...")
    qdrant_client.create_collection(
        collection_name=collection_name,
        vectors_config={
            "dense": models.VectorParams(
                size=384,
                distance=models.Distance.COSINE
            )
        },
        sparse_vectors_config={
            "sparse": models.SparseVectorParams()
        }
    )
    
    # 5. Upload points to Qdrant
    print("Preparing Qdrant points...")
    points = []
    for idx, c in enumerate(chunks):
        # Generate sparse vector
        sparse_indices, sparse_values = bm25_encoder.encode(c["embedded_text"])
        
        # Prepare Qdrant PointStruct
        point = models.PointStruct(
            id=idx,
            vector={
                "dense": dense_embeddings[idx].tolist(),
                "sparse": models.SparseVector(
                    indices=sparse_indices,
                    values=sparse_values
                )
            },
            payload={
                "text": c["raw_text"],
                "embedded_text": c["embedded_text"],
                "source_document": c["metadata"]["source_document"],
                "collection": c["metadata"]["collection"],
                "access_roles": c["metadata"]["access_roles"],
                "section_title": c["metadata"]["section_title"],
                "chunk_type": c["metadata"]["chunk_type"]
            }
        )
        points.append(point)
        
    print(f"Uploading {len(points)} points to Qdrant...")
    # Upload in batches
    batch_size = 100
    for i in range(0, len(points), batch_size):
        batch = points[i:i+batch_size]
        qdrant_client.upsert(
            collection_name=collection_name,
            points=batch
        )
        print(f"  Uploaded batch {i // batch_size + 1}/{(len(points) - 1) // batch_size + 1}")
        
    print("Ingestion and Vector Indexing completed successfully!")

if __name__ == '__main__':
    main()
