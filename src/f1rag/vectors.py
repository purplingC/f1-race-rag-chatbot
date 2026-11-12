"""ChromaDB storage for race documents: open the collection and replace a race's chunks"""

import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions

COLLECTION = "race_docs"


def open_client(path):
    """Open a ChromaDB client that keeps data in a folder, with anonymous telemetry off"""
    return chromadb.PersistentClient(path=str(path), settings=Settings(anonymized_telemetry=False))


def open_collection(path, embedding_function=None):
    """Open or create the race document collection, using the local default model unless given"""
    model = embedding_function or embedding_functions.DefaultEmbeddingFunction()
    return open_client(path).get_or_create_collection(
        COLLECTION, embedding_function=model, metadata={"hnsw:space": "cosine"}
    )


def replace_race_docs(collection, season, round_no, source_type, chunks):
    """Delete a race's chunks of one source type, add the new ones and return how many"""
    collection.delete(
        where={"$and": [{"season": season}, {"round": round_no}, {"source_type": source_type}]}
    )
    if chunks:
        collection.add(
            ids=[c["id"] for c in chunks],
            documents=[c["document"] for c in chunks],
            metadatas=[c["metadata"] for c in chunks],
        )
    return len(chunks)
