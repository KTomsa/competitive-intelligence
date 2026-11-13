"""Vstupní bod pro Vercel (WSGI). Veškerá logika je v balíčku competitive_intelligence."""
from competitive_intelligence.app import create_app

app = create_app()
