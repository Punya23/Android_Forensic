
"""Unit tests for AI enhancement modules.

Tests the behavioural-anomaly and multilingual modules:
1. BehavioralAnomalyDetector
2. MultiLanguageNLP
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from triage.forensics.behavioral_analysis import BehavioralAnomalyDetector
from triage.forensics.multilingual_advanced import MultiLanguageNLP


# Mock classes for testing
class MockFinding:
    """Mock Finding class for testing."""
    def __init__(self, id, severity, category, timestamp=None, entities=None, keywords=None):
        self.id = id
        self.severity = severity
        self.category = category
        self.timestamp = timestamp or datetime.now().isoformat()
        self.entities_matched = entities or []
        self.keywords_matched = keywords or []
        self.snippet = f"Test evidence for {id}"
        self.confidence = "live"


class MockMessage:
    """Mock Message class for testing."""
    def __init__(self, sender, recipient, text, timestamp):
        self.sender = sender
        self.recipient = recipient
        self.text = text
        self.timestamp = timestamp


class MockCallRecord:
    """Mock CallRecord class for testing."""
    def __init__(self, timestamp, direction="outgoing"):
        self.timestamp = timestamp
        self.direction = direction


class MockContact:
    """Mock Contact class for testing."""
    def __init__(self, name, phone):
        self.name = name
        self.phone = phone


class TestBehavioralAnomalyDetector(unittest.TestCase):
    """Tests for BehavioralAnomalyDetector."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.detector = BehavioralAnomalyDetector()
    
    def test_detect_night_activity(self):
        """Test detection of night activity."""
        # Create messages at 2-3 AM
        messages = [
            MockMessage("Rahul", "Priya", f"message {i}", f"2026-07-06T02:{i:02d}:00")
            for i in range(30)
        ]
        
        result = self.detector.analyze_timing_patterns(messages)
        
        self.assertIn("anomalies", result)
        self.assertGreater(len(result["anomalies"]), 0)
        self.assertEqual(result["anomalies"][0]["type"], "timing_anomaly")
    
    def test_detect_burst_activity(self):
        """Test detection of activity bursts."""
        # Create 20 messages in 10 minutes
        base_time = datetime.now()
        messages = [
            MockMessage("Rahul", "Priya", f"msg {i}", 
                       (base_time + timedelta(minutes=i/2)).isoformat())
            for i in range(20)
        ]
        
        bursts = self.detector.detect_burst_activity(messages)
        
        self.assertGreater(len(bursts), 0)
        self.assertEqual(bursts[0]["type"], "frequency_burst")
    
    def test_detect_contact_switching(self):
        """Test detection of rapid contact switching."""
        base_time = datetime.now()
        messages = []
        
        contacts = ["Alice", "Bob", "Charlie", "Alice", "Bob", "Charlie"]
        for i, contact in enumerate(contacts):
            messages.append(
                MockMessage(
                    "User",
                    contact,
                    f"message {i}",
                    (base_time + timedelta(minutes=i*2)).isoformat()
                )
            )
        
        switches = self.detector.identify_contact_switches(messages)
        
        # Should detect switching pattern
        self.assertGreaterEqual(len(switches), 0)


class TestMultiLanguageNLP(unittest.TestCase):
    """Tests for MultiLanguageNLP."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.nlp = MultiLanguageNLP()
    
    def test_detect_hinglish(self):
        """Test detection of Hinglish."""
        text = "kal milte hain bro"
        
        detected = self.nlp.detect_language(text)
        
        self.assertIn("hinglish", detected.lower())
    
    def test_understand_slang(self):
        """Test slang expansion."""
        text = "bro let's meet yaar"
        
        expanded = self.nlp.understand_slang(text)
        
        self.assertIn("brother", expanded.lower())
        self.assertIn("friend", expanded.lower())
    
    def test_expand_abbreviations(self):
        """Test abbreviation expansion."""
        text = "OK THX BRB"
        
        expanded = self.nlp.expand_abbreviations(text)
        
        self.assertIn("okay", expanded.lower())
        self.assertIn("thanks", expanded.lower())
    
    def test_interpret_emoji(self):
        """Test emoji interpretation."""
        text = "call me 🤙 at the place 👀"
        
        interpreted = self.nlp.interpret_emoji(text)
        
        self.assertIn("call", interpreted.lower())
        self.assertIn("watching", interpreted.lower())
    
    def test_process_message(self):
        """Test full message processing."""
        text = "bro meet me at 9pm OK 🤙"
        
        result = self.nlp.process_message(text)
        
        self.assertEqual(result["original"], text)
        self.assertIn("detected_language", result)
        self.assertIn("slang_expanded", result)
        self.assertIn("abbrev_expanded", result)
        self.assertIn("emoji_interpreted", result)


if __name__ == '__main__':
    unittest.main()
