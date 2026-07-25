"""Automated Section 65B Certificate generation."""
from typing import Dict, Any
import datetime

def generate_65b_certificate(case_meta: Dict[str, Any], examiner_name: str, designation: str) -> str:
    """Generate a Section 65B (Indian Evidence Act) certificate."""
    date = datetime.date.today().strftime("%d-%B-%Y")
    device_model = case_meta.get("device", {}).get("model", "Unknown Device")
    
    cert = f"""
    <h2>CERTIFICATE UNDER SECTION 65B OF THE INDIAN EVIDENCE ACT, 1872</h2>
    <p>I, <strong>{examiner_name}</strong>, working as <strong>{designation}</strong>, hereby certify that:</p>
    <ol>
        <li>The electronic record containing the forensic extraction of the mobile device <strong>{device_model}</strong> was produced by a computer/forensic workstation which was used regularly to store or process information.</li>
        <li>During the period of extraction, the computer was operating properly.</li>
        <li>The extraction process was read-only and no data on the original device was altered.</li>
        <li>The hashes generated match the extracted files.</li>
    </ol>
    <p>Date: {date}</p>
    <p>Signature: ___________________________</p>
    """
    return cert
