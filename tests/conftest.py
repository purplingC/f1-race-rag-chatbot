import pytest
from chromadb.api.types import EmbeddingFunction


class FakeEmbedding(EmbeddingFunction):
    """A tiny deterministic embedding so tests never download the real model"""

    def __init__(self):
        """Nothing to set up"""

    def __call__(self, input):
        """Turn each text into a small vector from simple counts"""
        return [[float(len(text) % 7), float(text.count("a")), 1.0] for text in input]

    @staticmethod
    def name():
        """Return the name chromadb stores with the collection"""
        return "fake-for-tests"

    def get_config(self):
        """Return the settings chromadb saves for this function, there are none"""
        return {}

    @staticmethod
    def build_from_config(config):
        """Rebuild the function from saved settings"""
        return FakeEmbedding()


@pytest.fixture
def fake_embedding():
    """Return the fake embedding function for vector store tests"""
    return FakeEmbedding()
