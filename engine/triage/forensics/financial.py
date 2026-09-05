"""Financial forensics module for transaction tracking and money trail analysis.

Detects UPI transactions, bank accounts, and builds money flow graphs
for financial investigation.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List, Optional


def detect_upi_transactions(messages: List[Dict]) -> List[Dict[str, Any]]:
    """Extract UPI payment transactions from message text.
    
    Args:
        messages: List of message dicts with 'text', 'sender', 'timestamp' fields
        
    Returns:
        List of transaction dicts:
        [{
            'sender': str,
            'receiver': str,
            'amount': float,
            'currency': str,
            'upi_id': str,
            'timestamp': str,
            'source_message': str,
            'confidence': float,
            'payment_app': str  # 'GPay', 'PhonePe', 'Paytm', etc.
        }]
    """
    transactions = []
    
    # UPI transaction patterns
    patterns = [
        # "Paid ₹500 to user@bank"
        r'(?:paid|sent|transferred)\s+₹?\s*(\d+(?:,\d{3})*(?:\.\d{2})?)\s+to\s+([a-zA-Z0-9._-]+@[a-zA-Z]+)',
        # "Received ₹500 from user@bank"
        r'(?:received|got)\s+₹?\s*(\d+(?:,\d{3})*(?:\.\d{2})?)\s+from\s+([a-zA-Z0-9._-]+@[a-zA-Z]+)',
        # "₹500 sent to user@bank"
        r'₹?\s*(\d+(?:,\d{3})*(?:\.\d{2})?)\s+sent\s+to\s+([a-zA-Z0-9._-]+@[a-zA-Z]+)',
        # "UPI payment of ₹500 to user@bank"
        r'upi\s+(?:payment|transfer).*?₹?\s*(\d+(?:,\d{3})*(?:\.\d{2})?).*?(?:to|for)\s+([a-zA-Z0-9._-]+@[a-zA-Z]+)',
    ]
    
    # Payment app keywords
    payment_apps = {
        'gpay': 'GPay',
        'googlepay': 'GPay',
        'phonepe': 'PhonePe',
        'paytm': 'Paytm',
        'bhim': 'BHIM',
        'whatsapp': 'WhatsApp Pay',
    }
    
    for msg in messages:
        text = msg.get('text', '')
        if not text or not isinstance(text, str):
            continue
        
        text_lower = text.lower()
        
        # Check if message contains payment keywords
        payment_keywords = ['upi', 'paid', 'sent', 'received', 'transfer', '₹', 'gpay', 'phonepe', 'paytm']
        if not any(keyword in text_lower for keyword in payment_keywords):
            continue
        
        # Try each pattern
        for pattern in patterns:
            matches = re.finditer(pattern, text_lower, re.IGNORECASE)
            
            for match in matches:
                amount_str = match.group(1).replace(',', '')
                upi_id = match.group(2)
                
                try:
                    amount = float(amount_str)
                except ValueError:
                    continue
                
                # Determine sender/receiver based on context
                sender = msg.get('sender', 'unknown')
                receiver = upi_id
                
                if any(word in text_lower for word in ['received', 'got', 'credited']):
                    # Reverse: sender is the UPI ID, receiver is message sender
                    sender = upi_id
                    receiver = msg.get('sender', 'unknown')
                
                # Detect payment app
                payment_app = 'UPI'
                for keyword, app_name in payment_apps.items():
                    if keyword in text_lower:
                        payment_app = app_name
                        break
                
                # Calculate confidence based on context
                confidence = 0.7
                if 'upi' in text_lower:
                    confidence += 0.1
                if any(app in text_lower for app in payment_apps.keys()):
                    confidence += 0.1
                if re.search(r'transaction.*(?:successful|complete)', text_lower):
                    confidence += 0.1
                
                confidence = min(confidence, 1.0)
                
                transactions.append({
                    'sender': sender,
                    'receiver': receiver,
                    'amount': amount,
                    'currency': 'INR',
                    'upi_id': upi_id,
                    'timestamp': msg.get('timestamp', ''),
                    'source_message': text[:200],
                    'confidence': confidence,
                    'payment_app': payment_app,
                })
    
    return transactions


def detect_bank_accounts(text: str) -> List[Dict[str, str]]:
    """Detect bank account numbers and IFSC codes in text.
    
    Args:
        text: Text to search for bank account information
        
    Returns:
        List of detected bank data:
        [
            {'type': 'account_number', 'value': '1234567890123', 'bank': 'SBI'},
            {'type': 'ifsc', 'value': 'SBIN0001234', 'bank': 'SBI'}
        ]
    """
    results = []
    
    if not text or not isinstance(text, str):
        return results
    
    # Account number pattern: 9-18 digits
    # Avoid matching phone numbers, amounts, etc.
    account_patterns = [
        r'\b(?:account|a/c|ac)\s*(?:no|number|#)?\s*:?\s*(\d{9,18})\b',
        r'\baccount\s+(\d{9,18})\b',
    ]
    
    for pattern in account_patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            account_num = match.group(1)
            
            # Try to find bank name in surrounding text
            context_start = max(0, match.start() - 50)
            context_end = min(len(text), match.end() + 50)
            context = text[context_start:context_end]
            
            bank_name = _extract_bank_name(context)
            
            results.append({
                'type': 'account_number',
                'value': account_num,
                'bank': bank_name,
                'context': context[:100]
            })
    
    # IFSC code pattern: 4 letters + 7 alphanumeric (AAAA0BBBBBB)
    ifsc_pattern = r'\b([A-Z]{4}0[A-Z0-9]{6})\b'
    matches = re.finditer(ifsc_pattern, text, re.IGNORECASE)
    
    for match in matches:
        ifsc_code = match.group(1).upper()
        
        # Extract bank name from IFSC code (first 4 letters)
        bank_code = ifsc_code[:4]
        bank_name = _get_bank_from_ifsc(bank_code)
        
        # Get context
        context_start = max(0, match.start() - 30)
        context_end = min(len(text), match.end() + 30)
        context = text[context_start:context_end]
        
        results.append({
            'type': 'ifsc',
            'value': ifsc_code,
            'bank': bank_name,
            'context': context[:100]
        })
    
    return results


def build_money_trail(transactions: List[Dict]) -> Dict[str, List[Dict]]:
    """Build money flow graph from transactions.
    
    Args:
        transactions: List of transaction dicts from detect_upi_transactions
        
    Returns:
        Dict mapping sender to list of outgoing transactions:
        {
            'user@bank': [
                {'to': 'recipient@bank', 'amount': 500, 'timestamp': '...', 'count': 3}
            ]
        }
    """
    # Build adjacency list
    money_flow = defaultdict(lambda: defaultdict(lambda: {
        'total_amount': 0,
        'transaction_count': 0,
        'transactions': []
    }))
    
    for txn in transactions:
        sender = txn.get('sender', 'unknown')
        receiver = txn.get('receiver', 'unknown')
        amount = txn.get('amount', 0)
        timestamp = txn.get('timestamp', '')
        
        if sender == 'unknown' or receiver == 'unknown':
            continue
        
        money_flow[sender][receiver]['total_amount'] += amount
        money_flow[sender][receiver]['transaction_count'] += 1
        money_flow[sender][receiver]['transactions'].append({
            'amount': amount,
            'timestamp': timestamp,
            'confidence': txn.get('confidence', 0.7)
        })
    
    # Convert to serializable format
    result = {}
    for sender, receivers in money_flow.items():
        result[sender] = []
        for receiver, data in receivers.items():
            result[sender].append({
                'to': receiver,
                'total_amount': data['total_amount'],
                'transaction_count': data['transaction_count'],
                'average_amount': data['total_amount'] / data['transaction_count'],
                'transactions': data['transactions'][:10],  # Limit to 10 for JSON size
                'suspicious': _is_suspicious_flow(data)
            })
    
    # Sort by total amount (descending)
    for sender in result:
        result[sender].sort(key=lambda x: x['total_amount'], reverse=True)
    
    return result


def _extract_bank_name(text: str) -> str:
    """Extract bank name from text context."""
    bank_keywords = {
        'sbi': 'State Bank of India',
        'hdfc': 'HDFC Bank',
        'icici': 'ICICI Bank',
        'axis': 'Axis Bank',
        'kotak': 'Kotak Mahindra Bank',
        'yes bank': 'Yes Bank',
        'pnb': 'Punjab National Bank',
        'bank of baroda': 'Bank of Baroda',
        'canara': 'Canara Bank',
        'union bank': 'Union Bank',
        'idbi': 'IDBI Bank',
        'indusind': 'IndusInd Bank',
    }
    
    text_lower = text.lower()
    for keyword, full_name in bank_keywords.items():
        if keyword in text_lower:
            return full_name
    
    return 'Unknown Bank'


def _get_bank_from_ifsc(ifsc_code: str) -> str:
    """Get bank name from IFSC code prefix."""
    ifsc_banks = {
        'SBIN': 'State Bank of India',
        'HDFC': 'HDFC Bank',
        'ICIC': 'ICICI Bank',
        'UTIB': 'Axis Bank',
        'KKBK': 'Kotak Mahindra Bank',
        'YESB': 'Yes Bank',
        'PUNB': 'Punjab National Bank',
        'BARB': 'Bank of Baroda',
        'CNRB': 'Canara Bank',
        'UBIN': 'Union Bank',
        'IBKL': 'IDBI Bank',
        'INDB': 'IndusInd Bank',
    }
    
    return ifsc_banks.get(ifsc_code, f'Bank ({ifsc_code})')


def _is_suspicious_flow(flow_data: Dict) -> bool:
    """Detect suspicious money flow patterns."""
    total_amount = flow_data['total_amount']
    txn_count = flow_data['transaction_count']
    
    # Suspicious if:
    # 1. Large total amount (>100k)
    if total_amount > 100000:
        return True
    
    # 2. Many small transactions (structuring)
    if txn_count > 10 and (total_amount / txn_count) < 10000:
        return True
    
    # 3. Round amounts (possible money laundering)
    transactions = flow_data['transactions']
    round_count = sum(1 for txn in transactions if txn['amount'] % 1000 == 0)
    if round_count > len(transactions) * 0.7:  # >70% round amounts
        return True
    
    return False
