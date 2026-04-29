"""ASGI entry shim so `uvicorn main:app` resolves the package app in `app/main.py`."""

from app.main import app

__all__ = ["app"]
