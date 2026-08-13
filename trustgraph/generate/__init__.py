"""Synthetic benchmark data generator.

Produces deterministic session JSON + ground truth (claims, supersession
chains, unresolved contradictions, QA pairs) for the conflict-heavy suite.
Deterministic now = reproducible benchmark later; an LLM paraphrase/noise
pass can be layered on top of the scripted events without changing ground
truth.
"""
