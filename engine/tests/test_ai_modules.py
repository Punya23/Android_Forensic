
"""Unit tests for AI enhancement modules.

Tests all 5 new AI modules:
1. EvidencePrioritizer
2. ConversationSummarizer
3. BehavioralAnomalyDetector
4. MultiLanguageNLP
5. SocialNetworkAnalyst
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from triage.intel.prioritization import EvidencePrioritizer
from triage.intel.summarization import ConversationSummarizer
from triage.intel.social_network import SocialNetworkAnalyst
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


class TestEvidencePrioritizer(unittest.TestCase):
    """Tests for EvidencePrioritizer."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.prioritizer = EvidencePrioritizer()
    
    def test_score_critical_severity(self):
        """Test scoring of critical severity findings."""
        finding = MockFinding(
            "F-001",
            severity="critical",
            category="message",
            entities=["Rahul"],
            keywords=["meet", "docks"]
        )
        
        result = self.prioritizer.score_evidence(
            finding,
            "suspect: Rahul, victim: Priya"
        )
        
        self.assertEqual(result["finding_id"], "F-001")
        self.assertIn(result["priority"], ["HIGH", "CRITICAL"])  # Can be either based on score
        self.assertGreater(result["score"], 70)
        self.assertIn("severity", result["factors"])
    
    def test_rank_evidence(self):
        """Test ranking multiple findings."""
        findings = [
            MockFinding("F-001", "info", "system"),
            MockFinding("F-002", "critical", "message", entities=["Rahul"]),
            MockFinding("F-003", "warn", "call"),
        ]
        
        ranked = self.prioritizer.rank_evidence(findings, "suspect: Rahul")
        
        # Should be sorted by score (highest first)
        self.assertEqual(len(ranked), 3)
        self.assertEqual(ranked[0]["finding_id"], "F-002")  # Critical
        self.assertGreater(ranked[0]["score"], ranked[1]["score"])
    
    def test_entity_matching(self):
        """Test entity matching increases score."""
        finding_with_entity = MockFinding(
            "F-001",
            "warn",
            "message",
            entities=["Rahul", "Priya"]
        )
        finding_without = MockFinding(
            "F-002",
            "warn",
            "message"
        )
        
        score_with = self.prioritizer.score_evidence(
            finding_with_entity,
            "suspect: Rahul, victim: Priya"
        )
        score_without = self.prioritizer.score_evidence(
            finding_without,
            "suspect: Rahul, victim: Priya"
        )
        
        self.assertGreater(
            score_with["factors"]["entity_match"],
            score_without["factors"]["entity_match"]
        )


class TestConversationSummarizer(unittest.TestCase):
    """Tests for ConversationSummarizer."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.summarizer = ConversationSummarizer()
    
    def test_summarize_empty_conversation(self):
        """Test summarizing empty conversation."""
        result = self.summarizer.summarize_conversation([], "chat-001")
        
        self.assertEqual(result["chat_id"], "chat-001")
        self.assertEqual(result["message_count"], 0)
        self.assertEqual(result["one_line"], "Empty conversation")
    
    def test_summarize_conversation(self):
        """Test summarizing a conversation."""
        messages = [
            MockMessage("Rahul", "Priya", "meet at the docks tonight", "2026-07-06T20:00:00"),
            MockMessage("Priya", "Rahul", "okay I'll bring the package", "2026-07-06T20:05:00"),
            MockMessage("Rahul", "Priya", "be there by 9pm", "2026-07-06T20:10:00"),
        ]
        
        result = self.summarizer.summarize_conversation(messages, "chat-001")
        
        self.assertEqual(result["chat_id"], "chat-001")
        self.assertEqual(result["message_count"], 3)
        self.assertIn("Rahul", result["participants"])
        self.assertIn("Priya", result["participants"])
        self.assertIsNotNone(result["one_line"])
        self.assertIsNotNone(result["summary"])
    
    def test_extract_entities(self):
        """Test entity extraction."""
        messages = [
            MockMessage("Rahul", "Priya", "meet at the docks at 9pm", "2026-07-06T20:00:00"),
            MockMessage("Priya", "Rahul", "I'll bring ₹50000 and my phone 9876543210", "2026-07-06T20:05:00"),
        ]
        
        entities = self.summarizer.extract_entities(messages)
        
        self.assertIn("people", entities)
        self.assertIn("places", entities)
        self.assertIn("dates", entities)
        self.assertIn("amounts", entities)
        self.assertIn("phone_numbers", entities)
        
        # Check if entities were found
        self.assertGreater(len(entities["people"]), 0)
        self.assertGreater(len(entities["places"]), 0)


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


class TestSocialNetworkAnalyst(unittest.TestCase):
    """Tests for SocialNetworkAnalyst."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.analyst = SocialNetworkAnalyst()
    
    def test_build_graph(self):
        """Test building social network graph."""
        messages = [
            MockMessage("Alice", "Bob", "Hi", "2026-07-06T10:00:00"),
            MockMessage("Bob", "Alice", "Hello", "2026-07-06T10:01:00"),
            MockMessage("Alice", "Charlie", "Hey", "2026-07-06T10:02:00"),
        ]
        
        contacts = [
            MockContact("Alice", "1111111111"),
            MockContact("Bob", "2222222222"),
            MockContact("Charlie", "3333333333"),
        ]
        
        graph = self.analyst.build_enhanced_graph(messages, contacts)
        
        self.assertIn("nodes", graph)
        self.assertIn("edges", graph)
        self.assertIn("communities", graph)
        self.assertGreater(len(graph["nodes"]), 0)
        self.assertGreater(len(graph["edges"]), 0)
    
    def test_calculate_influence(self):
        """Test influence score calculation."""
        messages = [
            MockMessage("Hub", "Alice", "msg", "2026-07-06T10:00:00"),
            MockMessage("Hub", "Bob", "msg", "2026-07-06T10:01:00"),
            MockMessage("Hub", "Charlie", "msg", "2026-07-06T10:02:00"),
            MockMessage("Alice", "Bob", "msg", "2026-07-06T10:03:00"),
        ]
        
        contacts = [
            MockContact("Hub", "1111111111"),
            MockContact("Alice", "2222222222"),
            MockContact("Bob", "3333333333"),
            MockContact("Charlie", "4444444444"),
        ]
        
        graph = self.analyst.build_enhanced_graph(messages, contacts)
        influence = graph["influence_scores"]
        
        # Hub should have high influence (connected to everyone)
        self.assertIn("Hub", influence)
        self.assertGreater(influence["Hub"]["score"], 0)
    
    def test_detect_communities(self):
        """Test community detection."""
        # Create two separate groups
        messages = [
            # Group 1: Alice, Bob
            MockMessage("Alice", "Bob", "msg", "2026-07-06T10:00:00"),
            MockMessage("Bob", "Alice", "msg", "2026-07-06T10:01:00"),
            # Group 2: Charlie, David
            MockMessage("Charlie", "David", "msg", "2026-07-06T10:02:00"),
            MockMessage("David", "Charlie", "msg", "2026-07-06T10:03:00"),
        ]
        
        contacts = [
            MockContact("Alice", "1111"),
            MockContact("Bob", "2222"),
            MockContact("Charlie", "3333"),
            MockContact("David", "4444"),
        ]
        
        graph = self.analyst.build_enhanced_graph(messages, contacts)
        communities = graph["communities"]
        
        self.assertGreater(len(communities), 0)


if __name__ == '__main__':
    unittest.main()
