import json
import os
from typing import Dict, Any, List

class JSONExporter:
    """
    Exports forensic data to JSON format.
    Supports nested data structures, API-ready formats, and pretty-printing.
    """
    
    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        
    def export(self, data: Dict[str, Any], filename: str, pretty: bool = True) -> str:
        """
        Exports the given dictionary data to a JSON file.
        
        Args:
            data: The dictionary data to export.
            filename: The name of the output JSON file.
            pretty: If True, pretty-prints the JSON.
            
        Returns:
            The path to the generated JSON file.
        """
        filepath = os.path.join(self.output_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            if pretty:
                json.dump(data, f, indent=4, ensure_ascii=False)
            else:
                json.dump(data, f, ensure_ascii=False)
                
        return filepath
    
    def export_ndjson(self, data_list: List[Dict[str, Any]], filename: str) -> str:
        """
        Exports a list of dictionaries to NDJSON (Newline Delimited JSON).
        """
        filepath = os.path.join(self.output_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            for item in data_list:
                f.write(json.dumps(item, ensure_ascii=False) + '\n')
                
        return filepath
