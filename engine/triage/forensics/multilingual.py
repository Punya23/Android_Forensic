import json
from pathlib import Path
from typing import List


class Translator:
    def __init__(self, dictionary_path: Path = None):
        self.dictionary_path = dictionary_path
        self.translations = {
            "hi": {
                "Report": "रिपोर्ट",
                "Evidence": "सबूत",
                "Scam Detection Analysis": "घोटाला पहचान विश्लेषण",
            },
            "ta": {
                "Report": "அறிக்கை",
                "Evidence": "சான்று",
                "Scam Detection Analysis": "மோசடி கண்டறிதல் பகுப்பாய்வு",
            },
            "te": {
                "Report": "నివేదిక",
                "Evidence": "సాక్ష్యం",
                "Scam Detection Analysis": "స్కామ్ డిటెక్షన్ అనాలిసిస్",
            },
            "kn": {
                "Report": "ವರದಿ",
                "Evidence": "ಪುರಾವೆ",
                "Scam Detection Analysis": "ಸ್ಕ್ಯಾಮ್ ಪತ್ತೆ ವಿಶ್ಲೇಷಣೆ",
            },
            "mr": {
                "Report": "अहवाल",
                "Evidence": "पुरावा",
                "Scam Detection Analysis": "घोटाळा शोध विश्लेषण",
            },
        }

        # Load external dict if provided
        if self.dictionary_path and self.dictionary_path.exists():
            try:
                with open(self.dictionary_path, "r", encoding="utf-8") as f:
                    ext_trans = json.load(f)
                    for lang, terms in ext_trans.items():
                        if lang not in self.translations:
                            self.translations[lang] = {}
                        self.translations[lang].update(terms)
            except Exception:
                pass

    def translate(self, text: str, target_lang: str) -> str:
        """Translate text to target language with fallback to English."""
        if target_lang == "en" or not text:
            return text

        lang_dict = self.translations.get(target_lang, {})
        # Simple exact match translation
        return lang_dict.get(text, text)

    def get_available_languages(self) -> List[str]:
        """Get list of available languages."""
        return ["en", "hi", "ta", "te", "kn", "mr"]

    def get_language_name(self, lang_code: str) -> str:
        """Get full language name from code."""
        names = {
            "en": "English",
            "hi": "Hindi",
            "ta": "Tamil",
            "te": "Telugu",
            "kn": "Kannada",
            "mr": "Marathi",
        }
        return names.get(lang_code, "Unknown")

    def translate_report(self, report_path: Path, target_lang: str) -> Path:
        """Translate HTML report to target language."""
        if target_lang == "en":
            return report_path

        if not report_path.exists():
            return report_path

        content = report_path.read_text(encoding="utf-8")

        # Extremely basic naive replacement for demo purposes.
        # A real implementation would parse the HTML and translate text nodes.
        lang_dict = self.translations.get(target_lang, {})
        for eng_term, trans_term in lang_dict.items():
            content = content.replace(eng_term, trans_term)

        out_path = report_path.with_name(
            f"{report_path.stem}_{target_lang}{report_path.suffix}"
        )
        out_path.write_text(content, encoding="utf-8")

        return out_path


# Procedural wrappers to match required signatures
_global_translator = Translator()


def translate(text: str, target_lang: str) -> str:
    return _global_translator.translate(text, target_lang)


def get_available_languages() -> List[str]:
    return _global_translator.get_available_languages()


def translate_report(report_path: Path, target_lang: str) -> Path:
    return _global_translator.translate_report(report_path, target_lang)


def get_language_name(lang_code: str) -> str:
    return _global_translator.get_language_name(lang_code)
