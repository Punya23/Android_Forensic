"""AI-Powered Forensic Intelligence Module.

This package contains machine learning, NLP, and advanced analytics tools 
for evidence triage. Heavy ML dependencies (like scikit-learn or transformers)
are imported lazily or wrapped in try/except blocks to ensure the core triage
engine doesn't crash if they are missing in the field.
"""
