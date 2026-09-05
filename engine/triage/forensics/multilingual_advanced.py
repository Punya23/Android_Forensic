"""Advanced Multi-Language NLP for Indian languages.

Enhances language understanding with slang expansion, abbreviation handling,
emoji interpretation, and code-switching detection for Hinglish/Tanglish/etc.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from ..models import Message
from ..intel.llm import LLMProvider, get_provider


class MultiLanguageNLP:
    """Enhanced NLP for Indian languages and code-switching."""
    
    # Indian slang dictionary (50+ terms)
    SLANG_MAP = {
        # Common Hindi/Hinglish
        "bro": "brother/close friend",
        "yaar": "friend",
        "arre": "hey",
        "accha": "okay/good",
        "theek": "okay/alright",
        "bas": "enough/that's it",
        "chalo": "let's go/okay",
        "kya": "what",
        "kaise": "how",
        "kahan": "where",
        "kab": "when",
        "kyun": "why",
        "haan": "yes",
        "nahi": "no",
        "matlab": "meaning/so",
        "bhai": "brother",
        "didi": "sister",
        "uncle": "uncle/older man",
        "aunty": "aunt/older woman",
        "beta": "child/dear",
        "abey": "hey (informal)",
        "oye": "hey",
        "sahi": "right/correct",
        "pakka": "sure/confirm",
        "achcha": "good/okay",
        "badiya": "great/excellent",
        "mast": "cool/great",
        "jugaad": "workaround/hack",
        "timepass": "casual activity",
        "bindas": "carefree/relaxed",
        "funda": "concept/idea",
        "scene": "situation",
        "paisa": "money",
        "kharcha": "expense",
        "dhanda": "business",
        "kaam": "work",
        "ghar": "home",
        "office": "workplace",
        "boss": "boss/friend",
        "dost": "friend",
        "sab": "all/everyone",
        "kuch": "something",
        "kal": "yesterday/tomorrow",
        "aaj": "today",
        "abhi": "now/right now",
        "baad": "later/after",
        "pehle": "before/first",
        "phir": "then/again",
        "wahan": "there",
        "yahan": "here",
        "suno": "listen",
        "dekho": "look/see",
        "batao": "tell",
        "chal": "move/go",
    }
    
    # Abbreviation expansion (30+ common)
    ABBREV_MAP = {
        "K": "okay",
        "OK": "okay",
        "PLZ": "please",
        "PLS": "please",
        "THX": "thanks",
        "THNX": "thanks",
        "TY": "thank you",
        "YT": "YouTube",
        "WA": "WhatsApp",
        "TG": "Telegram",
        "IG": "Instagram",
        "FB": "Facebook",
        "GM": "good morning",
        "GN": "good night",
        "BRB": "be right back",
        "GTG": "got to go",
        "IDK": "I don't know",
        "IMO": "in my opinion",
        "BTW": "by the way",
        "FYI": "for your information",
        "ASAP": "as soon as possible",
        "TBH": "to be honest",
        "NVM": "never mind",
        "OMG": "oh my god",
        "LOL": "laugh out loud",
        "ROFL": "rolling on floor laughing",
        "LMAO": "laughing my ass off",
        "SMH": "shaking my head",
        "WTF": "what the fuck",
        "AFAIK": "as far as I know",
        "IIRC": "if I recall correctly",
        "ETA": "estimated time of arrival",
        "FFS": "for fuck's sake",
        "JK": "just kidding",
    }
    
    # Emoji interpretation (Indian context)
    EMOJI_MAP = {
        "🤙": "call me / contact",
        "👀": "watching / observing / suspicious",
        "💀": "death / threat / danger / very funny",
        "🤷": "don't know / shrug / uncertain",
        "🙏": "please / thank you / respect / namaste",
        "👍": "okay / approved / like",
        "👎": "dislike / disapprove",
        "✌️": "peace / victory / bye",
        "🤝": "deal / agreement / handshake",
        "💪": "strength / power / strong",
        "🔥": "hot / trending / great",
        "💯": "perfect / 100 percent",
        "❤️": "love / like very much",
        "😂": "laughing / funny",
        "😭": "crying / sad / overwhelmed",
        "😊": "happy / smile",
        "😠": "angry / mad",
        "😡": "very angry / furious",
        "🤔": "thinking / wondering",
        "😎": "cool / confident",
        "🥳": "celebration / party",
        "🎉": "celebration / congrats",
        "💰": "money / payment",
        "💵": "money / dollars",
        "💳": "payment / card",
        "📱": "phone / mobile",
        "📞": "call / phone call",
        "💬": "message / chat",
        "🚗": "car / vehicle",
        "🏠": "home / house",
        "🏢": "office / building",
    }
    
    # Language script detection patterns
    SCRIPT_PATTERNS = {
        "devanagari": re.compile(r'[\u0900-\u097F]+'),  # Hindi
        "tamil": re.compile(r'[\u0B80-\u0BFF]+'),       # Tamil
        "telugu": re.compile(r'[\u0C00-\u0C7F]+'),      # Telugu
        "bengali": re.compile(r'[\u0980-\u09FF]+'),     # Bengali
        "gujarati": re.compile(r'[\u0A80-\u0AFF]+'),    # Gujarati
        "kannada": re.compile(r'[\u0C80-\u0CFF]+'),     # Kannada
        "malayalam": re.compile(r'[\u0D00-\u0D7F]+'),   # Malayalam
    }
    
    def __init__(self, provider: Optional[LLMProvider] = None):
        """Initialize NLP processor with optional LLM provider."""
        self.provider = provider or get_provider()
    
    def detect_language(self, text: str) -> str:
        """Detect language of text.
        
        Returns:
            Language code: 'hindi', 'tamil', 'telugu', 'hinglish', 'english', etc.
        """
        if not text:
            return "unknown"
        
        # Check for Indian scripts
        for lang, pattern in self.SCRIPT_PATTERNS.items():
            if pattern.search(text):
                # Check if mixed with English (code-switching)
                if re.search(r'[a-zA-Z]{3,}', text):
                    return f"{lang}_mixed"
                return lang
        
        # Check for Hinglish/Tanglish (English + Indian words)
        if self._is_code_switched(text):
            return "hinglish"  # Generic code-switching
        
        # Default to English
        return "english"
    
    def translate_context(self, text: str, target_lang: str = "english") -> str:
        """Context-aware translation using LLM.
        
        Args:
            text: Text to translate
            target_lang: Target language (default: english)
            
        Returns:
            Translated text
        """
        if not text:
            return ""
        
        detected = self.detect_language(text)
        
        # No translation needed if already English
        if detected == "english" and target_lang == "english":
            return text
        
        # Try LLM translation first
        if self.provider and self.provider.available:
            translated = self._llm_translate(text, detected, target_lang)
            if translated:
                return translated
        
        # Fall back to slang expansion
        return self.understand_slang(text)
    
    def understand_slang(self, text: str) -> str:
        """Expand Indian slang to standard English.
        
        Args:
            text: Text with slang
            
        Returns:
            Text with slang explained
        """
        expanded = text
        
        # Replace slang terms (case-insensitive)
        for slang, meaning in self.SLANG_MAP.items():
            # Use word boundaries to avoid partial matches
            pattern = r'\b' + re.escape(slang) + r'\b'
            replacement = f"{slang}[{meaning}]"
            expanded = re.sub(pattern, replacement, expanded, flags=re.IGNORECASE)
        
        return expanded
    
    def expand_abbreviations(self, text: str) -> str:
        """Expand common abbreviations.
        
        Args:
            text: Text with abbreviations
            
        Returns:
            Text with abbreviations expanded
        """
        expanded = text
        
        for abbrev, full in self.ABBREV_MAP.items():
            # Match whole word with word boundaries
            pattern = r'\b' + re.escape(abbrev) + r'\b'
            replacement = f"{abbrev}[{full}]"
            expanded = re.sub(pattern, replacement, expanded, flags=re.IGNORECASE)
        
        return expanded
    
    def interpret_emoji(self, text: str) -> str:
        """Explain emojis in Indian context.
        
        Args:
            text: Text with emojis
            
        Returns:
            Text with emoji meanings
        """
        interpreted = text
        
        for emoji, meaning in self.EMOJI_MAP.items():
            if emoji in text:
                interpreted = interpreted.replace(emoji, f"{emoji}[{meaning}]")
        
        return interpreted
    
    def detect_code_switching(self, text: str) -> dict:
        """Detect code-switching (Hinglish/Tanglish/etc).
        
        Returns:
            dict with is_code_switched, languages, confidence
        """
        if not text:
            return {
                "is_code_switched": False,
                "languages": [],
                "confidence": 0.0,
            }
        
        detected_langs = []
        
        # Check for Indian scripts
        for lang, pattern in self.SCRIPT_PATTERNS.items():
            if pattern.search(text):
                detected_langs.append(lang)
        
        # Check for English
        if re.search(r'[a-zA-Z]{3,}', text):
            detected_langs.append("english")
        
        # Check for Hinglish words
        hinglish_words = sum(1 for word in text.lower().split() if word in self.SLANG_MAP)
        
        is_code_switched = len(detected_langs) > 1 or hinglish_words > 0
        confidence = min(0.5 + (len(detected_langs) * 0.2) + (hinglish_words * 0.1), 1.0)
        
        return {
            "is_code_switched": is_code_switched,
            "languages": detected_langs,
            "confidence": confidence,
            "hinglish_words": hinglish_words,
        }
    
    def process_message(self, text: str) -> dict:
        """Process message with all NLP enhancements.
        
        Returns:
            dict with original, detected_language, translated, expanded, interpreted
        """
        if not text:
            return self._empty_result(text)
        
        detected = self.detect_language(text)
        translated = self.translate_context(text) if detected != "english" else text
        slang_expanded = self.understand_slang(translated)
        abbrev_expanded = self.expand_abbreviations(slang_expanded)
        emoji_interpreted = self.interpret_emoji(abbrev_expanded)
        code_switch = self.detect_code_switching(text)
        
        return {
            "original": text,
            "detected_language": detected,
            "translated": translated,
            "slang_expanded": slang_expanded,
            "abbrev_expanded": abbrev_expanded,
            "emoji_interpreted": emoji_interpreted,
            "code_switching": code_switch,
            "confidence": code_switch["confidence"],
        }
    
    def _is_code_switched(self, text: str) -> bool:
        """Check if text is code-switched (Hinglish/etc)."""
        # Check for Indian words in Latin script
        indian_words = 0
        words = text.lower().split()
        
        for word in words:
            if word in self.SLANG_MAP:
                indian_words += 1
        
        # If >20% words are Indian slang, it's code-switched
        return indian_words > len(words) * 0.2 if words else False
    
    def _llm_translate(self, text: str, source_lang: str, target_lang: str) -> Optional[str]:
        """Translate using LLM provider."""
        try:
            system = f"Translate from {source_lang} to {target_lang}. Maintain context and meaning."
            translated = self.provider.generate(system, text)
            
            if translated and len(translated) > 0:
                return translated.strip()
        except Exception:
            pass
        
        return None
    
    def _empty_result(self, text: str) -> dict:
        """Return empty result structure."""
        return {
            "original": text,
            "detected_language": "unknown",
            "translated": text,
            "slang_expanded": text,
            "abbrev_expanded": text,
            "emoji_interpreted": text,
            "code_switching": {
                "is_code_switched": False,
                "languages": [],
                "confidence": 0.0,
            },
            "confidence": 0.0,
        }
