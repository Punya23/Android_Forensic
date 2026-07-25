"""Evidence Export to Multiple Formats."""
import json
import csv
from pathlib import Path
from typing import List, Dict, Any

def export_to_json(data: List[Dict[str, Any]], out_path: Path) -> None:
    """Export evidence list to JSON."""
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def export_to_csv(data: List[Dict[str, Any]], out_path: Path) -> None:
    """Export evidence list to CSV."""
    if not data:
        return
        
    keys = set()
    for item in data:
        keys.update(item.keys())
        
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(keys))
        writer.writeheader()
        for item in data:
            # Flatten lists/dicts for CSV
            flat_item = {k: str(v) if isinstance(v, (list, dict)) else v for k, v in item.items()}
            writer.writerow(flat_item)

def export_to_format(data: List[Dict[str, Any]], out_path: Path, fmt: str = "json") -> None:
    """Export to specified format."""
    if fmt == "json":
        export_to_json(data, out_path)
    elif fmt == "csv":
        export_to_csv(data, out_path)
    else:
        raise ValueError(f"Unsupported format: {fmt}")
