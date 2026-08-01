import csv
import os
import pandas as pd
from typing import List, Dict, Any, Optional

class CSVExporter:
    """
    Exports forensic data to CSV/Excel format.
    Supports complex data flattening and aggregation using pandas.
    """
    
    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        
    def export_basic(self, data: List[Dict[str, Any]], filename: str, fieldnames: Optional[List[str]] = None) -> str:
        """
        Exports a flat list of dictionaries to a CSV file.
        """
        filepath = os.path.join(self.output_dir, filename)
        
        if not data:
            with open(filepath, 'w') as f:
                pass
            return filepath
            
        if not fieldnames:
            # Extract fieldnames from the first item
            fieldnames = list(data[0].keys())
            
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(data)
            
        return filepath
        
    def export_advanced(self, data: List[Dict[str, Any]], filename: str) -> str:
        """
        Exports using pandas for advanced normalization and potential Excel conversion.
        """
        filepath = os.path.join(self.output_dir, filename)
        
        # Use pandas to flatten and normalize
        df = pd.json_normalize(data)
        
        if filename.endswith('.xlsx'):
            df.to_excel(filepath, index=False)
        else:
            df.to_csv(filepath, index=False, encoding='utf-8')
            
        return filepath
