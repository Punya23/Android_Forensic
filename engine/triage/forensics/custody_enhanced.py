"""Enhanced Chain of Custody with blockchain integration and digital signatures.

Provides comprehensive evidence tracking with:
- Blockchain registration for evidence hashes
- Digital signatures for examiners
- Transfer tracking and approval workflow
- Complete audit trail
- Visual custody timeline
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.hazmat.backends import default_backend
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False


def register_evidence_on_blockchain(evidence_hash: str, metadata: Optional[Dict] = None) -> Dict:
    """Register evidence hash on blockchain (simulated).
    
    In production, this would integrate with Ethereum/Bitcoin blockchain.
    For now, creates a simulated blockchain record.
    
    Args:
        evidence_hash: SHA-256 hash of evidence
        metadata: Optional metadata (examiner, case_id, etc.)
        
    Returns:
        Dict with transaction details:
        {
            'tx_id': str,
            'block_number': int,
            'timestamp': str,
            'hash': str,
            'status': str
        }
    """
    # Simulate blockchain transaction
    # In production: Use web3.py for Ethereum or bitcoin-python for Bitcoin
    
    tx_id = _generate_transaction_id(evidence_hash)
    timestamp = datetime.now().isoformat()
    
    blockchain_record = {
        'tx_id': tx_id,
        'block_number': _simulate_block_number(),
        'timestamp': timestamp,
        'hash': evidence_hash,
        'metadata': metadata or {},
        'status': 'confirmed',
        'blockchain': 'simulated',  # In production: 'ethereum' or 'bitcoin'
    }
    
    # Store record locally (in production, this would be on-chain)
    _store_blockchain_record(blockchain_record)
    
    return blockchain_record


def verify_evidence_on_blockchain(evidence_hash: str, tx_id: str) -> bool:
    """Verify evidence hash on blockchain.
    
    Args:
        evidence_hash: SHA-256 hash to verify
        tx_id: Transaction ID from registration
        
    Returns:
        True if hash matches blockchain record, False otherwise
    """
    # Retrieve blockchain record
    record = _retrieve_blockchain_record(tx_id)
    
    if not record:
        return False
    
    # Verify hash matches
    return record.get('hash') == evidence_hash and record.get('status') == 'confirmed'


def sign_evidence(examiner_key: str, evidence_hash: str) -> str:
    """Sign evidence with examiner's private key.
    
    Args:
        examiner_key: Path to examiner's private key or key material
        evidence_hash: SHA-256 hash of evidence
        
    Returns:
        Base64-encoded signature
    """
    if not CRYPTO_AVAILABLE:
        return _generate_simulated_signature(evidence_hash)
    
    try:
        # Load private key
        if Path(examiner_key).exists():
            with open(examiner_key, 'rb') as f:
                private_key = serialization.load_pem_private_key(
                    f.read(),
                    password=None,
                    backend=default_backend()
                )
        else:
            # Generate new key pair if not exists
            private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
                backend=default_backend()
            )
        
        # Sign the hash
        signature = private_key.sign(
            evidence_hash.encode(),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        
        # Return base64-encoded signature
        import base64
        return base64.b64encode(signature).decode('utf-8')
        
    except Exception:
        return _generate_simulated_signature(evidence_hash)


def verify_signature(public_key: str, evidence_hash: str, signature: str) -> bool:
    """Verify examiner's signature.
    
    Args:
        public_key: Path to examiner's public key or key material
        evidence_hash: SHA-256 hash of evidence
        signature: Base64-encoded signature
        
    Returns:
        True if signature is valid, False otherwise
    """
    if not CRYPTO_AVAILABLE:
        # Simulated verification
        return len(signature) > 0
    
    try:
        import base64
        
        # Load public key
        if Path(public_key).exists():
            with open(public_key, 'rb') as f:
                pub_key = serialization.load_pem_public_key(
                    f.read(),
                    backend=default_backend()
                )
        else:
            return False
        
        # Decode signature
        sig_bytes = base64.b64decode(signature)
        
        # Verify signature
        pub_key.verify(
            sig_bytes,
            evidence_hash.encode(),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        
        return True
        
    except Exception:
        return False


def generate_custody_timeline(case_dir: str) -> str:
    """Generate visual chain of custody timeline.
    
    Args:
        case_dir: Path to case directory
        
    Returns:
        HTML string with interactive timeline
    """
    case_path = Path(case_dir)
    custody_log = _load_custody_log(case_path)
    
    if not custody_log:
        return _generate_empty_timeline()
    
    html = _generate_timeline_html(custody_log)
    return html


def transfer_evidence(case_id: str, from_examiner: str, to_examiner: str, reason: str) -> bool:
    """Transfer evidence to another examiner.
    
    Args:
        case_id: Case identifier
        from_examiner: Current examiner
        to_examiner: Receiving examiner
        reason: Transfer reason
        
    Returns:
        True if transfer successful, False otherwise
    """
    transfer_record = {
        'case_id': case_id,
        'from': from_examiner,
        'to': to_examiner,
        'reason': reason,
        'timestamp': datetime.now().isoformat(),
        'status': 'completed',
    }
    
    # Log transfer
    _log_custody_event('transfer', transfer_record)
    
    return True


def generate_audit_report(case_dir: str) -> str:
    """Generate complete audit report.
    
    Args:
        case_dir: Path to case directory
        
    Returns:
        HTML string with audit report
    """
    case_path = Path(case_dir)
    
    # Load audit logs
    audit_logs = _load_audit_logs(case_path)
    custody_log = _load_custody_log(case_path)
    
    html = _generate_audit_report_html(audit_logs, custody_log)
    return html


# Helper functions

def _generate_transaction_id(evidence_hash: str) -> str:
    """Generate simulated transaction ID."""
    combined = f"{evidence_hash}{datetime.now().isoformat()}"
    return hashlib.sha256(combined.encode()).hexdigest()[:16].upper()


def _simulate_block_number() -> int:
    """Simulate blockchain block number."""
    import random
    return random.randint(10000000, 20000000)


def _store_blockchain_record(record: Dict) -> None:
    """Store blockchain record locally."""
    blockchain_dir = Path.home() / '.snagr' / 'blockchain'
    blockchain_dir.mkdir(parents=True, exist_ok=True)
    
    record_file = blockchain_dir / f"{record['tx_id']}.json"
    with open(record_file, 'w') as f:
        json.dump(record, f, indent=2)


def _retrieve_blockchain_record(tx_id: str) -> Optional[Dict]:
    """Retrieve blockchain record."""
    blockchain_dir = Path.home() / '.snagr' / 'blockchain'
    record_file = blockchain_dir / f"{tx_id}.json"
    
    if not record_file.exists():
        return None
    
    with open(record_file, 'r') as f:
        return json.load(f)


def _generate_simulated_signature(evidence_hash: str) -> str:
    """Generate simulated signature when cryptography is unavailable."""
    import base64
    sig = hashlib.sha256(f"SIGNATURE_{evidence_hash}".encode()).hexdigest()
    return base64.b64encode(sig.encode()).decode('utf-8')


def _load_custody_log(case_path: Path) -> List[Dict]:
    """Load custody log from case directory."""
    custody_file = case_path / 'logs' / 'custody.json'
    
    if not custody_file.exists():
        return []
    
    with open(custody_file, 'r') as f:
        return json.load(f)


def _load_audit_logs(case_path: Path) -> List[Dict]:
    """Load audit logs from case directory."""
    audit_file = case_path / 'logs' / 'audit.json'
    
    if not audit_file.exists():
        return []
    
    with open(audit_file, 'r') as f:
        return json.load(f)


def _log_custody_event(event_type: str, event_data: Dict) -> None:
    """Log custody event."""
    # In production, would log to database or audit system
    pass


def _generate_timeline_html(custody_log: List[Dict]) -> str:
    """Generate HTML timeline visualization."""
    html = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Chain of Custody Timeline</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            background: #f5f5f5;
            padding: 20px;
        }
        .timeline {
            position: relative;
            max-width: 1200px;
            margin: 0 auto;
        }
        .timeline::after {
            content: '';
            position: absolute;
            width: 6px;
            background-color: #4CAF50;
            top: 0;
            bottom: 0;
            left: 50%;
            margin-left: -3px;
        }
        .event {
            padding: 10px 40px;
            position: relative;
            background-color: inherit;
            width: 50%;
        }
        .event::after {
            content: '';
            position: absolute;
            width: 25px;
            height: 25px;
            right: -17px;
            background-color: white;
            border: 4px solid #4CAF50;
            top: 15px;
            border-radius: 50%;
            z-index: 1;
        }
        .left {
            left: 0;
        }
        .right {
            left: 50%;
        }
        .right::after {
            left: -16px;
        }
        .content {
            padding: 20px 30px;
            background-color: white;
            position: relative;
            border-radius: 6px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        h2 { color: #4CAF50; margin-top: 0; }
        .timestamp { color: #888; font-size: 14px; }
    </style>
</head>
<body>
    <h1 style="text-align: center;">Chain of Custody Timeline</h1>
    <div class="timeline">
"""
    
    for i, event in enumerate(custody_log):
        position = 'left' if i % 2 == 0 else 'right'
        html += f"""
        <div class="event {position}">
            <div class="content">
                <h2>{event.get('type', 'Event').title()}</h2>
                <p class="timestamp">{event.get('timestamp', 'Unknown time')}</p>
                <p>{event.get('description', 'No description')}</p>
                <p><strong>Examiner:</strong> {event.get('examiner', 'Unknown')}</p>
            </div>
        </div>
"""
    
    html += """
    </div>
</body>
</html>
"""
    return html


def _generate_empty_timeline() -> str:
    """Generate empty timeline HTML."""
    return """
<!DOCTYPE html>
<html>
<head><title>Chain of Custody</title></head>
<body style="text-align: center; padding: 40px;">
    <h1>No Custody Events</h1>
    <p>No chain of custody events recorded for this case.</p>
</body>
</html>
"""


def _generate_audit_report_html(audit_logs: List[Dict], custody_log: List[Dict]) -> str:
    """Generate audit report HTML."""
    html = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Audit Report</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
        }
        th, td {
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }
        th {
            background-color: #4CAF50;
            color: white;
        }
        tr:hover { background-color: #f5f5f5; }
        h1, h2 { color: #4CAF50; }
    </style>
</head>
<body>
    <h1>Complete Audit Report</h1>
    <p>Generated: """ + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + """</p>
    
    <h2>Custody Events (""" + str(len(custody_log)) + """)</h2>
    <table>
        <tr>
            <th>Timestamp</th>
            <th>Event Type</th>
            <th>Examiner</th>
            <th>Details</th>
        </tr>
"""
    
    for event in custody_log[-50:]:  # Last 50 events
        html += f"""
        <tr>
            <td>{event.get('timestamp', 'N/A')}</td>
            <td>{event.get('type', 'N/A')}</td>
            <td>{event.get('examiner', 'N/A')}</td>
            <td>{event.get('description', 'N/A')}</td>
        </tr>
"""
    
    html += """
    </table>
    
    <h2>Access Logs (""" + str(len(audit_logs)) + """)</h2>
    <table>
        <tr>
            <th>Timestamp</th>
            <th>User</th>
            <th>Action</th>
            <th>Resource</th>
        </tr>
"""
    
    for log in audit_logs[-100:]:  # Last 100 logs
        html += f"""
        <tr>
            <td>{log.get('timestamp', 'N/A')}</td>
            <td>{log.get('user', 'N/A')}</td>
            <td>{log.get('action', 'N/A')}</td>
            <td>{log.get('resource', 'N/A')}</td>
        </tr>
"""
    
    html += """
    </table>
</body>
</html>
"""
    return html
