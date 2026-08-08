"""
SNAGR — Android Rapid Evidence Triage & Forensic Preview engine.

A forensically-minded, minimally-invasive logical acquisition + preview engine for
Android devices. Every artifact is hashed at the moment of extraction, every action
that touches the device is written to an append-only audit log, and recovered/carved
data is always labelled with its provenance and confidence.

This package is deliberately UI-agnostic: it is driven by the CLI (`triage.cli`) and by
the local Flask service (`triage.server`) that the Electron dashboard talks to.
"""

__version__ = "0.1.0"
TOOL_NAME = "SNAGR Triage Engine"
