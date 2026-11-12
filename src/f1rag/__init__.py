"""F1 Race Summarizer & RAG Chatbot"""

from importlib.metadata import PackageNotFoundError, version

# The version lives only in pyproject.toml; this reads it from the installed package
try:
    __version__ = version("f1rag")
except PackageNotFoundError:
    __version__ = "unknown"
