"""Errors shared by the ingestion sources"""


class RateLimitError(Exception):
    """Raised when a source keeps answering 429 after every retry, so the run should stop"""
