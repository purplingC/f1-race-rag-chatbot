"""Load one race's Wikipedia text into the vector store"""

from f1rag import vectors
from f1rag.ingest import chunking


def ingest_race_text(collection, wiki, season, round_no, meeting, ingested_at):
    """Find the race's article, chunk it and replace the race's wiki chunks, returning the count"""
    article = wiki.article_for_race(season, meeting["meeting_name"], meeting["location"])
    chunks = chunking.chunk_article(article, season, round_no, ingested_at)
    return vectors.replace_race_docs(collection, season, round_no, "wiki", chunks)
