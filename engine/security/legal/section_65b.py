import logging
import time
import hashlib
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class Section65BCertificateGenerator:
    """
    Generates Court-admissible Section 65B (Indian Evidence Act) Certificates 
    for extracted digital forensics evidence.
    """
    def __init__(self, hsm_manager: Optional[Any] = None):
        """
        Args:
            hsm_manager: Optional Hardware Security Module for cryptographic signing.
        """
        self.hsm_manager = hsm_manager
        
    def generate_certificate(self, case_id: str, investigator_name: str, 
                             device_details: Dict[str, str], evidence_hash: str) -> Dict[str, Any]:
        """
        Produces the formal Section 65B statement affirming device integrity and hashing.
        """
        logger.info(f"Generating Section 65B Certificate for case {case_id}")
        
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        
        # 65B(4) Legal Declarations
        declaration = (
            f"I, {investigator_name}, hereby certify under Section 65B of the Indian Evidence Act, 1872:\n"
            f"1. The electronic record (Hash: {evidence_hash}) was produced by the computer/mobile device "
            f"described as {device_details.get('make', 'Unknown')} {device_details.get('model', 'Unknown')} "
            f"(IMEI/Serial: {device_details.get('serial', 'Unknown')}).\n"
            f"2. During the period over which the computer was used to store or process information, "
            f"it was operating properly.\n"
            f"3. The hash value {evidence_hash} confirms the integrity of the cloned data, which has not been altered."
        )
        
        # Digital Signature Verification (if HSM provided)
        signature = None
        if self.hsm_manager:
            signature = self.hsm_manager.sign_evidence(evidence_hash)
        else:
            # Fallback to standard software hashing for the certificate itself
            cert_raw = declaration + timestamp
            signature = hashlib.sha256(cert_raw.encode()).hexdigest()
            
        certificate = {
            "case_id": case_id,
            "certificate_type": "Section_65B_Evidence_Act",
            "timestamp": timestamp,
            "investigator": investigator_name,
            "device": device_details,
            "evidence_hash": evidence_hash,
            "declaration_text": declaration,
            "digital_signature": signature
        }
        
        return certificate
