"""Acquisition sources — the abstraction that lets the pipeline run identically against
a real ADB-connected device or a mock device backed by a fixtures directory."""

from .base import AcquisitionSource, PulledFile
from .real import RealDeviceSource
from .mock import MockDeviceSource

__all__ = ["AcquisitionSource", "PulledFile", "RealDeviceSource", "MockDeviceSource"]
