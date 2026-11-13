"""Vercel WSGI entry point. Thin adapter only; all logic lives in the package."""

from competitive_intelligence.app import create_app

app = create_app()
