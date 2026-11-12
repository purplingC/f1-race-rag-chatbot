def test_package_imports():
    """Check that the f1rag package can be imported"""
    import f1rag

    assert f1rag.__name__ == "f1rag"


def test_version_comes_from_the_installed_package():
    """Check that the package version is read from pyproject.toml through the install"""
    import importlib.metadata

    import f1rag

    assert f1rag.__version__ == importlib.metadata.version("f1rag")


def test_user_agents_carry_the_package_version():
    """Check that both clients send the project name with its current version"""
    import f1rag
    from f1rag.ingest import openf1, wikipedia

    expected = f"f1-race-rag-chatbot/{f1rag.__version__}"
    assert openf1.USER_AGENT == expected
    assert wikipedia.USER_AGENT == expected
