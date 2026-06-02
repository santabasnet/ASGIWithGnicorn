"""
tests/conftest.py — pytest configuration.

Policy & Technical Details:
- Configuration:
  * Sets the default asyncio mode to auto for pytest-asyncio integration.

Writer: Santa, Wiseyak
Date: 2026-06-02
"""


def pytest_configure(config):
    """Set asyncio mode to auto so all async tests/fixtures are handled automatically."""
    config.addinivalue_line("markers", "asyncio: mark test as async")
