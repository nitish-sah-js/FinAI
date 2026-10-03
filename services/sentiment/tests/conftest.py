"""Shared fixtures for sentiment service tests."""

import os
import pytest

# Force MOCK mode OFF for unit tests — we mock at the function level instead
os.environ.pop("MOCK", None)
