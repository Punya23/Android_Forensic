"""Profile-Based Optimization — learning from previous runs.

Analyzes past acquisition runs to predict file pull times and determine the optimal
file extraction order, prioritizing files that were previously successful or fast.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Constants
PROFILES_ROOT = Path("cases") / "profiles"


def load_profile(device_id: str) -> Dict[str, Any]:
    """Load optimization profile for a specific device."""
    if not device_id:
        return _empty_profile()
        
    profile_path = PROFILES_ROOT / f"{device_id}.json"
    if not profile_path.exists():
        return _empty_profile()
        
    try:
        return json.loads(profile_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load profile for %s: %s", device_id, exc)
        return _empty_profile()


def save_profile(device_id: str, profile: Dict[str, Any]) -> None:
    """Save optimization profile for a specific device."""
    if not device_id:
        return
        
    PROFILES_ROOT.mkdir(parents=True, exist_ok=True)
    profile_path = PROFILES_ROOT / f"{device_id}.json"
    
    try:
        # Atomic write
        fd, tmp = tempfile.mkstemp(dir=PROFILES_ROOT, prefix=".tmp_")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(profile, fh, indent=2)
        os.replace(tmp, profile_path)
    except Exception as exc:
        logger.warning("Failed to save profile for %s: %s", device_id, exc)


def analyze_previous_runs(device_id: str) -> Dict[str, Any]:
    """Analyze previous runs to find optimization opportunities."""
    profile = load_profile(device_id)
    runs = profile.get("runs", [])
    
    if not runs:
        return {"has_history": False}
        
    # Calculate average pull times per file
    file_times: Dict[str, List[float]] = {}
    for run in runs:
        for file_path, elapsed in run.get("file_timings", {}).items():
            file_times.setdefault(file_path, []).append(elapsed)
            
    avg_times = {f: sum(t)/len(t) for f, t in file_times.items()}
    
    # Identify fast files (under 1 second) and slow files
    fast_files = [f for f, t in avg_times.items() if t < 1.0]
    slow_files = [f for f, t in avg_times.items() if t > 10.0]
    
    return {
        "has_history": True,
        "run_count": len(runs),
        "avg_times": avg_times,
        "fast_files": fast_files,
        "slow_files": slow_files,
    }


def get_optimal_file_order(device_id: str, files: List[str]) -> List[str]:
    """Get optimal extraction order based on history."""
    if not files:
        return []
        
    analysis = analyze_previous_runs(device_id)
    if not analysis.get("has_history"):
        return files # No history, return as-is
        
    avg_times = analysis.get("avg_times", {})
    
    # Sort files: known fast files first, then unknown files, then slow files
    # Default time for unknown files is 5.0 seconds
    def _sort_key(f: str) -> float:
        return avg_times.get(f, 5.0)
        
    return sorted(files, key=_sort_key)


def get_estimated_time(device_id: str, files: List[str]) -> float:
    """Estimate total extraction time based on history."""
    if not files:
        return 0.0
        
    analysis = analyze_previous_runs(device_id)
    avg_times = analysis.get("avg_times", {})
    
    total_time = 0.0
    for f in files:
        total_time += avg_times.get(f, 2.0) # Assume 2.0s for unknown files
        
    return total_time


def update_profile(device_id: str, run_data: Dict[str, Any]) -> None:
    """Update profile with data from a new run."""
    if not device_id:
        return
        
    profile = load_profile(device_id)
    
    # Keep only the last 10 runs
    runs = profile.get("runs", [])
    runs.append(run_data)
    if len(runs) > 10:
        runs = runs[-10:]
        
    profile["runs"] = runs
    profile["last_updated"] = run_data.get("timestamp")
    
    save_profile(device_id, profile)


def _empty_profile() -> Dict[str, Any]:
    return {"runs": [], "last_updated": None}
