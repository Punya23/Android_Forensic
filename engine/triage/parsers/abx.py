"""Minimal, dependency-free reader for ABX (Android Binary XML).

Forensic purpose
----------------
From Android 12 (API 31) onward ``android.util.Xml`` defaults to a binary
serialisation (``persist.sys.binary_xml``, default true), so system XML files —
``/data/system_ce/<u>/recent_tasks/<id>_task.xml``, ``packages.xml``,
``appops.xml`` and friends — are ABX **despite still being named ``.xml``**.
A parser that trusts the extension silently produces "no records found" on every
modern device, which is exactly the absent-vs-inaccessible confusion this project
must never make. This module decodes ABX into a standard
``xml.etree.ElementTree.Element`` so ordinary XML code keeps working.

Wire format (AOSP ``BinaryXmlSerializer`` / ``BinaryXmlPullParser`` /
``FastDataInput``)::

    magic     := "ABX\\x00"        (4 bytes; byte 3 is the format version, only 0 exists)
    token     := 1 byte;  event = token & 0x0f,  type = token & 0xf0
    all multibyte integers are BIG-ENDIAN (java.io.DataOutput semantics)

Limitations — read before relying on the output
-----------------------------------------------
* **Only format version 0 is understood.** A different version byte raises
  :class:`AbxDecodeError` rather than being decoded on a guess.
* **ElementTree cannot hold duplicate attribute names.** AOSP legitimately emits
  several attributes literally named ``category`` on one ``<categories>`` element.
  Rather than silently dropping them, repeats are stored as ``name#2``, ``name#3``
  … (see :data:`DUPLICATE_ATTR_SEPARATOR`). Callers that care about categories must
  account for this; it is a lossless-but-renamed representation, not the original.
* **Namespaces do not exist in ABX at all** (``getNamespace()`` returns
  ``NO_NAMESPACE``), so none are reconstructed.
* ``TYPE_INT_HEX`` / ``TYPE_LONG_HEX`` are rendered as unsigned zero-padded hex
  strings, matching what a text-XML dump of the same file would contain. That is a
  rendering choice; the wire bytes are identical to the non-hex types.
* This decoder is verified against the AOSP token/type tables, not against a
  byte-exact capture from every OEM build. Vendor-modified serialisers are
  possible; an unexpected token raises rather than being skipped, because a
  desynchronised stream produces plausible-looking garbage, which is worse than
  an error.
"""

from __future__ import annotations

import base64
import os
import struct
import xml.etree.ElementTree as ET
from typing import Any, List, Optional, Union

# ---------------------------------------------------------------------------
# Constants (verbatim from AOSP BinaryXmlSerializer)
# ---------------------------------------------------------------------------
ABX_MAGIC: bytes = b"ABX\x00"

#: Only version 0 of the container exists as of Android 16.
ABX_SUPPORTED_VERSION: int = 0

# Low nibble — XmlPullParser event codes. Only 0-4 and 15 ever appear in practice.
EVENT_START_DOCUMENT = 0
EVENT_END_DOCUMENT = 1
EVENT_START_TAG = 2
EVENT_END_TAG = 3
EVENT_TEXT = 4
EVENT_CDSECT = 5
EVENT_ENTITY_REF = 6
EVENT_IGNORABLE_WHITESPACE = 7
EVENT_PROCESSING_INSTRUCTION = 8
EVENT_COMMENT = 9
EVENT_DOCDECL = 10
EVENT_ATTRIBUTE = 15  # AOSP-internal: `static final int ATTRIBUTE = 15;`

# High nibble — data types.
TYPE_NULL = 0x10
TYPE_STRING = 0x20
TYPE_STRING_INTERNED = 0x30
TYPE_BYTES_HEX = 0x40
TYPE_BYTES_BASE64 = 0x50
TYPE_INT = 0x60
TYPE_INT_HEX = 0x70
TYPE_LONG = 0x80
TYPE_LONG_HEX = 0x90
TYPE_FLOAT = 0xA0
TYPE_DOUBLE = 0xB0
TYPE_BOOLEAN_TRUE = 0xC0
TYPE_BOOLEAN_FALSE = 0xD0

#: ``FastDataInput`` sentinel meaning "a new interned string follows inline".
INTERN_NEW_STRING = 0xFFFF

#: Cap on the interned pool. Past this, new strings are written inline but are NOT
#: appended to the pool — mirroring that is mandatory or the indices desynchronise.
MAX_INTERNED = 0xFFFF

#: Separator used when an element carries the same attribute name more than once.
DUPLICATE_ATTR_SEPARATOR = "#"


class AbxDecodeError(Exception):
    """Raised when a byte stream is not decodable as ABX version 0.

    Callers are expected to catch this and degrade honestly (record a caveat naming
    the file as *present but not decoded*), never to treat it as "nothing found".
    """


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
def is_abx(data_or_path: Union[bytes, bytearray, str, "os.PathLike[str]"]) -> bool:
    """True if the buffer / file begins with the 4-byte ``ABX\\x00`` magic.

    Mirrors what AOSP's ``Xml.resolvePullParser`` does (a 4-byte ``pread`` compared
    against the magic) and what ALEAPP's ``checkabx`` does. We deliberately do NOT
    sniff for ``<?xml`` as the negative case: some text files have no declaration,
    so the only reliable positive test is the magic.

    Never raises — an unreadable path is simply "not ABX" from this function's point
    of view, and the caller reports the read failure.
    """
    if isinstance(data_or_path, (bytes, bytearray)):
        return bytes(data_or_path[:4]) == ABX_MAGIC
    try:
        with open(data_or_path, "rb") as fh:
            return fh.read(4) == ABX_MAGIC
    except (OSError, TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Decoder
# ---------------------------------------------------------------------------
class _Reader:
    """Cursor over the ABX byte buffer.

    Every read is bounds-checked: a truncated file must raise :class:`AbxDecodeError`
    at the point of truncation rather than wrap around into unrelated bytes and
    fabricate attribute values.
    """

    __slots__ = ("buf", "pos", "pool")

    def __init__(self, buf: bytes) -> None:
        self.buf = buf
        self.pos = 4  # past the magic
        self.pool: List[str] = []

    def _take(self, n: int) -> bytes:
        end = self.pos + n
        if n < 0 or end > len(self.buf):
            raise AbxDecodeError(
                f"truncated ABX stream: wanted {n} byte(s) at offset {self.pos}, "
                f"only {len(self.buf) - self.pos} remain"
            )
        chunk = self.buf[self.pos : end]
        self.pos = end
        return chunk

    def u8(self) -> int:
        return self._take(1)[0]

    def u16(self) -> int:
        return struct.unpack(">H", self._take(2))[0]

    def utf(self) -> str:
        """u16 *byte* length followed by that many UTF-8 bytes.

        ``errors="replace"`` is deliberate: one undecodable string must cost us that
        string, not the whole task record. (AOSP's writer uses standard 4-byte UTF-8
        sequences, so this should not normally trigger.)
        """
        return self._take(self.u16()).decode("utf-8", "replace")

    def interned(self) -> str:
        """Read an interned string reference, extending the pool when required."""
        ref = self.u16()
        if ref == INTERN_NEW_STRING:
            s = self.utf()
            if len(self.pool) < MAX_INTERNED:
                self.pool.append(s)
            return s
        if ref >= len(self.pool):
            raise AbxDecodeError(
                f"interned string reference {ref} at offset {self.pos - 2} is out of "
                f"range (pool holds {len(self.pool)}); the stream is desynchronised"
            )
        return self.pool[ref]

    def value(self, type_nibble: int) -> Any:
        """Consume the payload for ``type_nibble`` and return a Python value."""
        if type_nibble == TYPE_NULL:
            return None
        if type_nibble == TYPE_BOOLEAN_TRUE:
            return True
        if type_nibble == TYPE_BOOLEAN_FALSE:
            return False
        if type_nibble == TYPE_STRING:
            return self.utf()
        if type_nibble == TYPE_STRING_INTERNED:
            return self.interned()
        if type_nibble in (TYPE_BYTES_HEX, TYPE_BYTES_BASE64):
            raw = self._take(self.u16())
            return raw.hex() if type_nibble == TYPE_BYTES_HEX else base64.b64encode(
                raw
            ).decode("ascii")
        if type_nibble == TYPE_INT:
            return struct.unpack(">i", self._take(4))[0]
        if type_nibble == TYPE_INT_HEX:
            # Rendered unsigned: ARGB colours have the alpha bit set and would
            # otherwise surface as a confusing negative decimal.
            return "%08x" % (struct.unpack(">i", self._take(4))[0] & 0xFFFFFFFF)
        if type_nibble == TYPE_LONG:
            return struct.unpack(">q", self._take(8))[0]
        if type_nibble == TYPE_LONG_HEX:
            return "%016x" % (
                struct.unpack(">q", self._take(8))[0] & 0xFFFFFFFFFFFFFFFF
            )
        if type_nibble == TYPE_FLOAT:
            return struct.unpack(">f", self._take(4))[0]
        if type_nibble == TYPE_DOUBLE:
            return struct.unpack(">d", self._take(8))[0]
        raise AbxDecodeError(
            f"unsupported ABX data type 0x{type_nibble:02x} at offset {self.pos - 1}"
        )


def _stringify(value: Any) -> str:
    """Render a decoded value the way the equivalent text XML would carry it."""
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def _set_attribute(el: ET.Element, name: str, value: str) -> None:
    """Set an attribute, renaming repeats instead of silently overwriting them.

    AOSP writes ``<categories category=... category=... />``, which no XML DOM can
    represent. Dropping the extras would lose evidence (intent categories), so
    repeats become ``category#2``, ``category#3`` …
    """
    if name not in el.attrib:
        el.set(name, value)
        return
    n = 2
    while f"{name}{DUPLICATE_ATTR_SEPARATOR}{n}" in el.attrib:
        n += 1
    el.set(f"{name}{DUPLICATE_ATTR_SEPARATOR}{n}", value)


def decode_abx(data: bytes) -> ET.Element:
    """Decode an ABX buffer into an ``ElementTree.Element``.

    Raises :class:`AbxDecodeError` on a bad magic, an unsupported container version,
    truncation, a desynchronised interned-string reference, or an unknown token —
    never a partially-populated tree, which would look like real but incomplete
    evidence.

    If the file has several top-level elements (some AOSP files do; ``recent_tasks``
    does not) they are wrapped in a synthetic ``<abx_multi_root>`` element so no
    data is discarded. Callers expecting a single root should check ``root.tag``.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise AbxDecodeError(f"expected bytes, got {type(data).__name__}")
    data = bytes(data)
    if len(data) < 4 or data[:3] != ABX_MAGIC[:3]:
        raise AbxDecodeError("buffer does not start with the ABX magic ('ABX\\x00')")
    if data[3] != ABX_SUPPORTED_VERSION:
        raise AbxDecodeError(
            f"ABX container version {data[3]} is not supported (only version 0 is "
            "documented); refusing to guess at the layout"
        )

    r = _Reader(data)
    roots: List[ET.Element] = []
    stack: List[ET.Element] = []

    while r.pos < len(data):
        token = r.u8()
        event = token & 0x0F
        type_nibble = token & 0xF0

        if event == EVENT_START_TAG:
            name = r.interned()
            el = ET.Element(name)
            if stack:
                stack[-1].append(el)
            else:
                roots.append(el)
            stack.append(el)

        elif event == EVENT_ATTRIBUTE:
            # The attribute NAME is always an interned string regardless of the
            # type nibble; only the value follows the type.
            name = r.interned()
            value = r.value(type_nibble)
            if not stack:
                raise AbxDecodeError(
                    f"ATTRIBUTE token at offset {r.pos} with no open element"
                )
            _set_attribute(stack[-1], name, _stringify(value))

        elif event == EVENT_END_TAG:
            # The name is still on the wire and MUST be consumed, or every
            # subsequent read desynchronises silently.
            r.interned()
            if not stack:
                raise AbxDecodeError(
                    f"END_TAG token at offset {r.pos} with no matching START_TAG"
                )
            stack.pop()

        elif event == EVENT_TEXT:
            text = r.value(type_nibble)
            if stack and text is not None:
                target = stack[-1]
                chunk = _stringify(text)
                if len(target):
                    last = target[-1]
                    last.tail = (last.tail or "") + chunk
                else:
                    target.text = (target.text or "") + chunk

        elif event == EVENT_START_DOCUMENT:
            # Optional in ABX — AOSP tolerates its absence, so we must too.
            r.value(type_nibble)

        elif event == EVENT_END_DOCUMENT:
            r.value(type_nibble)
            break

        elif event in (
            EVENT_CDSECT,
            EVENT_ENTITY_REF,
            EVENT_IGNORABLE_WHITESPACE,
            EVENT_PROCESSING_INSTRUCTION,
            EVENT_COMMENT,
            EVENT_DOCDECL,
        ):
            # Consume the payload so the cursor stays aligned; the content itself
            # carries no forensic value for the artifacts we parse.
            r.value(type_nibble)

        else:
            raise AbxDecodeError(
                f"unsupported ABX event 0x{event:x} (token 0x{token:02x}) at offset "
                f"{r.pos - 1}"
            )

    if not roots:
        raise AbxDecodeError("ABX stream decoded to no elements at all")
    if len(roots) == 1:
        return roots[0]
    wrapper = ET.Element("abx_multi_root")
    for el in roots:
        wrapper.append(el)
    return wrapper


def parse_xml_or_abx(path: Union[str, "os.PathLike[str]"]) -> Optional[ET.Element]:
    """Parse ``path`` as either text XML or ABX, auto-detected from the magic bytes.

    Returns the root ``Element``, or ``None`` if the file is missing, unreadable or
    undecodable. **Never raises** — the caller records a caveat naming the file as
    present-but-not-decoded, which is a different finding from "no data".
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except (OSError, TypeError, ValueError):
        return None

    if not data:
        return None

    if data[:4] == ABX_MAGIC:
        try:
            return decode_abx(data)
        except AbxDecodeError:
            return None
        except Exception:  # a malformed file must never take the run down
            return None

    try:
        return ET.fromstring(data.decode("utf-8", "replace"))
    except (ET.ParseError, ValueError, UnicodeError):
        return None
    except Exception:
        return None
