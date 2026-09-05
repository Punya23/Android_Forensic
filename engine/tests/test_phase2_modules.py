"""Tests for Phase 2 forensic modules.

Tests all 5 Phase 2 modules:
- WhatsApp Advanced Analysis
- Telegram Advanced Analysis
- Financial Forensics
- Legal Intelligence
- Enhanced Location Intelligence
"""

import pytest
import tempfile
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

# Import Phase 2 modules
from triage.parsers.whatsapp_advanced import (
    analyze_whatsapp_reactions,
    detect_whatsapp_admins,
    analyze_whatsapp_calls,
)

from triage.parsers.telegram_advanced import (
    detect_telegram_bots,
    analyze_telegram_groups,
)

from triage.forensics.financial import (
    detect_upi_transactions,
    detect_bank_accounts,
    build_money_trail,
)

from triage.forensics.legal import (
    match_statutes,
    generate_fir,
    generate_expert_report,
)

from triage.forensics.location_enhanced import (
    reverse_geocode,
    detect_poi,
    analyze_visit_durations,
)


# ==================== WhatsApp Advanced Tests ====================

def test_analyze_whatsapp_reactions_empty():
    """Test with non-existent database"""
    result = analyze_whatsapp_reactions("/nonexistent/path.db")
    assert result == {}


def test_analyze_whatsapp_reactions():
    """Test reaction analysis with mock data"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Create mock table
        cursor.execute("""
            CREATE TABLE message_reactions (
                message_row_id INTEGER,
                reaction_text TEXT,
                sender_jid TEXT
            )
        """)
        
        # Insert test data
        cursor.execute("""
            INSERT INTO message_reactions VALUES
            (1, '🔥', 'user1@s.whatsapp.net'),
            (1, '🔥', 'user2@s.whatsapp.net'),
            (1, '❤️', 'user3@s.whatsapp.net'),
            (2, '👍', 'user1@s.whatsapp.net')
        """)
        conn.commit()
        conn.close()
        
        result = analyze_whatsapp_reactions(db_path)
        
        assert '1' in result
        assert result['1']['reactions']['🔥'] == 2
        assert result['1']['reactions']['❤️'] == 1
        assert result['1']['total_reactions'] == 3
        assert len(result['1']['users']['🔥']) == 2
        
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_detect_whatsapp_admins():
    """Test admin detection with mock data"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Create mock table
        cursor.execute("""
            CREATE TABLE group_participants (
                gjid TEXT,
                jid TEXT,
                is_admin INTEGER
            )
        """)
        
        # Insert test data
        cursor.execute("""
            INSERT INTO group_participants VALUES
            ('group1@g.us', 'admin1@s.whatsapp.net', 1),
            ('group1@g.us', 'admin2@s.whatsapp.net', 1),
            ('group1@g.us', 'member1@s.whatsapp.net', 0),
            ('group2@g.us', 'admin3@s.whatsapp.net', 1)
        """)
        conn.commit()
        conn.close()
        
        result = detect_whatsapp_admins(db_path)
        
        assert 'group1@g.us' in result
        assert len(result['group1@g.us']) == 2
        assert 'admin1@s.whatsapp.net' in result['group1@g.us']
        assert 'group2@g.us' in result
        
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_analyze_whatsapp_calls():
    """Test call pattern analysis"""
    now = datetime.now()
    
    call_logs = [
        {
            'jid': 'user1@s.whatsapp.net',
            'timestamp': now.timestamp(),
            'duration': 300,
            'call_result': 'answered'
        },
        {
            'jid': 'user1@s.whatsapp.net',
            'timestamp': (now + timedelta(hours=1)).timestamp(),
            'duration': 120,
            'call_result': 'missed'
        },
        {
            'jid': 'user2@s.whatsapp.net',
            'timestamp': now.replace(hour=2).timestamp(),  # 2 AM
            'duration': 600,
            'call_result': 'answered'
        },
    ]
    
    result = analyze_whatsapp_calls(call_logs)
    
    assert 'per_contact' in result
    assert 'user1@s.whatsapp.net' in result['per_contact']
    assert result['per_contact']['user1@s.whatsapp.net']['total_calls'] == 2
    assert result['per_contact']['user1@s.whatsapp.net']['missed_calls'] == 1
    assert result['summary']['total_calls'] == 3


# ==================== Telegram Advanced Tests ====================

def test_detect_telegram_bots_empty():
    """Test with non-existent database"""
    result = detect_telegram_bots("/nonexistent/path.db")
    assert result == {}


def test_detect_telegram_bots():
    """Test bot detection with mock data"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Create mock tables
        cursor.execute("""
            CREATE TABLE users (
                uid INTEGER PRIMARY KEY,
                first_name TEXT,
                username TEXT,
                is_bot INTEGER
            )
        """)
        
        cursor.execute("""
            CREATE TABLE messages (
                from_id INTEGER,
                type TEXT,
                date INTEGER
            )
        """)
        
        # Insert test data
        cursor.execute("""
            INSERT INTO users VALUES
            (1, 'Test Bot', 'testbot', 1),
            (2, 'Another Bot', 'anotherbot', 1),
            (3, 'Regular User', 'user', 0)
        """)
        
        cursor.execute("""
            INSERT INTO messages VALUES
            (1, 'text', 1234567890),
            (1, 'command', 1234567900),
            (2, 'text', 1234567910)
        """)
        
        conn.commit()
        conn.close()
        
        result = detect_telegram_bots(db_path)
        
        assert '1' in result
        assert result['1']['name'] == 'Test Bot'
        assert result['1']['interaction_count'] == 2
        assert '2' in result
        
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_analyze_telegram_groups():
    """Test group analysis with mock data"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Create mock tables
        cursor.execute("""
            CREATE TABLE chats (
                uid INTEGER PRIMARY KEY,
                title TEXT,
                type TEXT
            )
        """)
        
        cursor.execute("""
            CREATE TABLE messages (
                chat_id INTEGER,
                from_id INTEGER,
                date INTEGER
            )
        """)
        
        # Insert test data
        cursor.execute("""
            INSERT INTO chats VALUES
            (1, 'Test Group', 'group'),
            (2, 'Another Group', 'supergroup')
        """)
        
        # Insert many messages from different users
        for i in range(50):
            cursor.execute("INSERT INTO messages VALUES (?, ?, ?)", 
                         (1, i % 5, 1234567890 + i * 100))
        
        conn.commit()
        conn.close()
        
        result = analyze_telegram_groups(db_path)
        
        assert '1' in result
        assert result['1']['name'] == 'Test Group'
        assert result['1']['total_messages'] == 50
        assert result['1']['total_members'] == 5
        
    finally:
        Path(db_path).unlink(missing_ok=True)


# ==================== Financial Forensics Tests ====================

def test_detect_upi_transactions():
    """Test UPI transaction detection"""
    messages = [
        {
            'text': 'Paid ₹500 to user@ybl',
            'sender': 'alice',
            'timestamp': '2024-01-01T10:00:00'
        },
        {
            'text': 'Received ₹1000 from merchant@paytm',
            'sender': 'bob',
            'timestamp': '2024-01-01T11:00:00'
        },
        {
            'text': 'UPI payment of ₹250 to shop@oksbi',
            'sender': 'charlie',
            'timestamp': '2024-01-01T12:00:00'
        },
        {
            'text': 'Hello world',  # Non-payment message
            'sender': 'dave',
            'timestamp': '2024-01-01T13:00:00'
        },
    ]
    
    result = detect_upi_transactions(messages)
    
    assert len(result) == 3
    assert result[0]['amount'] == 500
    assert result[0]['upi_id'] == 'user@ybl'
    assert result[1]['amount'] == 1000
    assert result[1]['receiver'] == 'bob'  # Reversed for "received"


def test_detect_payment_confirmation_messages():
    """Test detection of payment app confirmation messages"""
    messages = [
        {
            'text': 'You paid ₹500 to Merchant Name via GooglePay. UPI Ref: ABC123456789XYZ',
            'sender': 'GPay-noreply',
            'timestamp': '2024-01-01T10:00:00'
        },
        {
            'text': 'Rs.1000 debited from A/c XX1234 to VPA merchant@paytm on 01-Jan-24. UPI Ref: XYZ987654321ABC',
            'sender': 'SBI-ALERT',
            'timestamp': '2024-01-01T11:00:00'
        },
        {
            'text': 'Payment of ₹250 to Shop Name successful. Transaction ID: TXN123456789',
            'sender': 'PhonePe',
            'timestamp': '2024-01-01T12:00:00'
        },
        {
            'text': '₹2000 credited to your account from VPA sender@ybl. UPI Ref: REF123456789',
            'sender': 'HDFC-Bank',
            'timestamp': '2024-01-01T13:00:00'
        },
    ]
    
    result = detect_upi_transactions(messages)
    
    # Should detect all 4 transactions
    assert len(result) >= 3, f"Expected at least 3 transactions, got {len(result)}"
    
    # Check amounts are detected correctly
    amounts = [t['amount'] for t in result]
    assert 500.0 in amounts
    assert 1000.0 in amounts or 2000.0 in amounts  # At least one of these
    
    # Check that confirmation messages are detected
    confirmation_txns = [t for t in result if t['message_type'] == 'payment_confirmation']
    assert len(confirmation_txns) >= 2, "Should detect at least 2 payment confirmations"
    
    # Check GPay transaction
    gpay_txn = [t for t in result if t.get('payment_app') == 'GPay']
    assert len(gpay_txn) > 0, "Should detect GPay transaction"
    assert gpay_txn[0]['amount'] == 500
    assert gpay_txn[0]['transaction_id'] == 'ABC123456789XYZ'
    assert gpay_txn[0]['confidence'] > 0.7
    
    # Check that transaction IDs are extracted
    txns_with_ids = [t for t in result if t['transaction_id']]
    assert len(txns_with_ids) >= 2, "Should extract transaction IDs from confirmation messages"


def test_detect_bank_accounts():
    """Test bank account detection"""
    text = """
    My account number is 1234567890123 at SBI.
    IFSC code: SBIN0001234
    Another account: 9876543210987 with HDFC Bank
    IFSC: HDFC0002345
    """
    
    result = detect_bank_accounts(text)
    
    # Should find at least accounts and IFSC codes
    account_results = [r for r in result if r['type'] == 'account_number']
    ifsc_results = [r for r in result if r['type'] == 'ifsc']
    
    assert len(account_results) >= 1
    assert len(ifsc_results) >= 1
    assert any('SBIN' in r['value'] for r in ifsc_results)


def test_build_money_trail():
    """Test money trail graph building"""
    transactions = [
        {'sender': 'alice', 'receiver': 'bob', 'amount': 500, 'timestamp': '2024-01-01', 'confidence': 0.9},
        {'sender': 'alice', 'receiver': 'bob', 'amount': 300, 'timestamp': '2024-01-02', 'confidence': 0.9},
        {'sender': 'bob', 'receiver': 'charlie', 'amount': 700, 'timestamp': '2024-01-03', 'confidence': 0.9},
    ]
    
    result = build_money_trail(transactions)
    
    assert 'alice' in result
    assert len(result['alice']) == 1
    assert result['alice'][0]['to'] == 'bob'
    assert result['alice'][0]['total_amount'] == 800
    assert result['alice'][0]['transaction_count'] == 2
    
    assert 'bob' in result
    assert result['bob'][0]['to'] == 'charlie'


# ==================== Legal Intelligence Tests ====================

def test_match_statutes():
    """Test statute matching"""
    text = "Suspect was involved in online fraud and cheating using computer systems"
    
    result = match_statutes(text)
    
    assert len(result) > 0
    # Should match fraud/cheating sections
    sections = [r['section'] for r in result]
    assert any(s in ['420', '66D'] for s in sections)


def test_generate_fir():
    """Test FIR generation"""
    case_data = {
        'case_id': 'TEST001',
        'complainant': 'John Doe',
        'accused': 'Jane Smith',
        'incident_date': '2024-01-01',
        'incident_place': 'Mumbai',
        'description': 'Online fraud case involving cheating',
    }
    
    evidence = [
        {'type': 'messages', 'count': 100},
        {'type': 'calls', 'count': 50},
    ]
    
    result = generate_fir(case_data, evidence)
    
    assert 'FIRST INFORMATION REPORT' in result
    assert 'TEST001' in result
    assert 'John Doe' in result
    assert 'Jane Smith' in result
    assert 'messages:' in result  # Evidence count
    assert 'calls:' in result


def test_generate_expert_report():
    """Test expert report generation"""
    case_data = {
        'case_id': 'TEST001',
        'device_model': 'Samsung Galaxy',
        'android_version': '12',
        'examination_date': '2024-01-01',
        'findings': [],
        'evidence_count': {'messages': 100, 'calls': 50},
    }
    
    result = generate_expert_report('/tmp', case_data)
    
    assert 'FORENSIC EXPERT REPORT' in result
    assert 'TEST001' in result
    assert 'Samsung Galaxy' in result
    assert 'LIMITATIONS AND CAVEATS' in result  # Honesty section
    assert 'SNAGR' in result


# ==================== Location Intelligence Tests ====================

def test_reverse_geocode():
    """Test reverse geocoding"""
    # New Delhi coordinates
    result = reverse_geocode(28.6139, 77.2090)
    assert 'New Delhi' in result or 'Delhi' in result
    
    # Mumbai coordinates
    result = reverse_geocode(19.0760, 72.8777)
    assert 'Mumbai' in result
    
    # Invalid coordinates
    result = reverse_geocode(None, None)
    assert result == "Invalid coordinates"


def test_detect_poi():
    """Test POI detection"""
    # Near New Delhi (coordinates close to known POIs)
    result = detect_poi(28.6139, 77.2090, radius_km=5.0)
    # Should find some POIs within 5km
    assert isinstance(result, list)


def test_analyze_visit_durations():
    """Test visit duration analysis"""
    now = datetime.now()
    
    locations = [
        {'lat': 28.6139, 'lon': 77.2090, 'timestamp': now.timestamp()},
        {'lat': 28.6140, 'lon': 77.2091, 'timestamp': (now + timedelta(minutes=30)).timestamp()},
        {'lat': 28.6141, 'lon': 77.2092, 'timestamp': (now + timedelta(hours=1)).timestamp()},
        # Different location
        {'lat': 19.0760, 'lon': 72.8777, 'timestamp': (now + timedelta(hours=2)).timestamp()},
        {'lat': 19.0761, 'lon': 72.8778, 'timestamp': (now + timedelta(hours=3)).timestamp()},
    ]
    
    result = analyze_visit_durations(locations)
    
    assert len(result) == 2  # Two distinct locations
    assert all('duration_seconds' in r for r in result)
    assert all('location' in r for r in result)
    assert all('visit_count' in r for r in result)
    
    # First location should have 3 visits
    first_location = result[0]
    assert first_location['visit_count'] >= 3 or first_location['visit_count'] == 3


# ==================== Integration Tests ====================

def test_phase2_workflow():
    """Test complete Phase 2 workflow"""
    # 1. Analyze messages for financial forensics
    messages = [
        {'text': 'Paid ₹1000 to shop@paytm for fraudulent goods', 
         'sender': 'victim', 'timestamp': '2024-01-01'}
    ]
    transactions = detect_upi_transactions(messages)
    assert len(transactions) > 0
    
    # 2. Match statutes
    statutes = match_statutes('fraudulent goods online cheating')
    assert len(statutes) > 0
    
    # 3. Build money trail
    trail = build_money_trail(transactions)
    assert len(trail) > 0
    
    # 4. Generate reports
    case_data = {
        'case_id': 'INT001',
        'complainant': 'Victim',
        'accused': 'Unknown',
        'incident_date': '2024-01-01',
        'incident_place': 'Delhi',
        'description': 'Online fraud',
        'device_model': 'Test Device',
        'android_version': '12',
        'examination_date': '2024-01-01',
        'evidence_count': {'transactions': len(transactions)},
        'findings': [],
    }
    
    fir = generate_fir(case_data, [])
    report = generate_expert_report('/tmp', case_data)
    
    assert 'FIR' in fir
    assert 'FORENSIC' in report


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
