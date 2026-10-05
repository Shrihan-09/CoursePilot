"""Deterministic Rutgers section scheduling (Phase 6.6).

Chooses SECTIONS of exactly the requested courses for one term. It never
chooses courses (that is app.services.planning), never registers, reserves
or touches WebReg, and calls no LLM. See docs/DATA_MODEL.md section 41.
"""
