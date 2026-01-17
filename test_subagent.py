"""Test module for hello_world function."""

def hello_world():
    """Return the string 'Hello, World!'"""
    return 'Hello, World!'


def test_hello_world():
    """Test the hello_world function."""
    assert hello_world() == 'Hello, World!'