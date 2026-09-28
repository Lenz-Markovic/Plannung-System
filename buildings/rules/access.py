"""
Does the reader need access to apartments / the boiler room?
Port of zugangErkennen() from the prototype (spec section 6: "automatische
Erkennung aus den Vermerken (Aufmaß, manuell ablesen, RWM-Prüfung,
NE-Nummern, Schlüssel)").

Input: the reading type and the free-text notes of a building.
Output: an Access object (see below). Pure function, no database.

Note on the regular expressions: JavaScript's \\b only knows ASCII letters,
so an "ä" counts as a word boundary there. To get the same results as the
prototype we use our own ASCII boundaries (_B / _E) instead of \\b.
"""

import re
from dataclasses import dataclass, field

_B = r"(?<![A-Za-z0-9_])"   # start of a word (ASCII, like \b in JavaScript)
_E = r"(?![A-Za-z0-9_])"    # end of a word
I = re.IGNORECASE


@dataclass
class Access:
    apartment: bool = False          # reader has to enter apartments
    room: bool = False               # boiler room / cellar
    units: list = field(default_factory=list)    # e.g. ["NE003", "NE006"]
    reasons: list = field(default_factory=list)  # why
    key_hint: str = ""               # sentence about keys
    announcement_hint: str = ""      # sentence about announcing the date

    @property
    def only_reading_type(self):
        """Access only because of the reading type (e.g. 'manuell'), no special task in the notes."""
        return self.apartment and all(reason.startswith("Ableseart") for reason in self.reasons)

    @property
    def important(self):
        return self.apartment and not self.only_reading_type

    def text(self):
        """One line for plan and print-out (zugangText() in the prototype)."""
        parts = []
        if self.apartment:
            parts.append("🔑 Zugang zur Wohnung notwendig" + (f" ({', '.join(self.units)})" if self.units else "")
                         + (f": {', '.join(self.reasons)}" if self.reasons else ""))
        if self.room:
            parts.append("🚪 Zugang Heizraum/Keller")
        if self.key_hint:
            parts.append(f"Schlüssel: {self.key_hint}")
        if self.announcement_hint:
            parts.append(f"Anmeldung: {self.announcement_hint}")
        return " · ".join(parts)


def _unit(number):
    return "NE" + str(number).zfill(3)


def detect_access(reading_type, *notes):
    """reading_type: e.g. 'MANU - Betreten der Wohnung'; notes: remark, note, handwritten note, ..."""
    text = " · ".join(n for n in notes if n)
    reading_type = reading_type or ""
    result = Access()
    reasons, units = [], set()

    def has(pattern):
        return re.search(pattern, text, I) is not None

    if re.search(r"Betreten der Wohnung", reading_type, I):
        result.apartment = True
        reasons.append("Ableseart: Wohnungen betreten")
    if re.search(r"manuelle Ablesung|Verdunster", reading_type, I):
        result.apartment = True
        reasons.append("Ableseart: manuelle Ablesung")
    if re.search(r"Betreten des Kellers", reading_type, I):
        result.room = True

    # Apartment numbers: NE003, NE 3, "004/006:", "Nutzer 012"
    for match in re.findall(_B + r"NE\s?0*\d{1,3}" + _E, text, I):
        units.add(_unit(re.sub(r"\D", "", match)))
    for match in re.finditer(r"(?:^|[\s.;,(])0(\d{2})((?:\s*[/+]\s*0?\d{2,3})*)\s*:", text):
        units.add("NE0" + match.group(1))
        for extra in re.findall(r"\d{2,3}", match.group(2)):
            units.add(_unit(extra))
    for match in re.findall(r"Nutzer\s0*\d{1,3}", text, I):
        units.add(_unit(re.sub(r"\D", "", match)))

    if has(r"aufma(ß|ss)"):
        result.apartment = True
        reasons.append("Aufmaß machen")
    if has(r"manuell|ohne funk|keine funk|nicht (per )?funk"):
        result.apartment = True
        reasons.append("Geräte manuell ablesen")
    if (has(r"(rwm|rauchwarnmelder|rauchmelder)[^.;]{0,40}(pr(ü|ue)f|wartung|test)")
            and not has(r"keine (rwm|rauchwarnmelder)[^.;]{0,30}(pr(ü|ue)f|wartung)|keine rwm-wartung")):
        result.apartment = True
        reasons.append("RWM-Prüfung in den Wohnungen")
    if has(r"nachfragen|nutzer (fragen|ansprechen|befragen)|mieter (fragen|ansprechen)"):
        result.apartment = True
        reasons.append("Nutzer vor Ort fragen")
    if has(_B + r"(austausch|ger(ä|ae)tetausch|z(ä|ae)hlertausch|tausch|umr(ü|ue)st\w*|nachr(ü|ue)st\w*)" + _E):
        reasons.append("Tausch/Umrüstung erwähnt – prüfen")
    if has(r"zugang[^.;]{0,40}(über|ueber|durch|via)\s+(die\s+)?(NE\s?0*\d|wohnung)"):
        result.apartment = True
        reasons.append("Zugang nur über eine Wohnung")
    if units and has(r"(NE\s?0*\d{1,3}|0\d{2}\s*:)[^.;]{0,80}(ablesen|pr(ü|ue)f|notier|aufnehm|messen|zugang|zähler|zaehler|wmz|wwz|kwz|hkv)"):
        result.apartment = True
        if "Aufgabe in einzelnen Wohnungen" not in reasons:
            reasons.append("Aufgabe in einzelnen Wohnungen")
    if has(r"heizraum|" + _B + r"HR" + _E + r"|HR-Zugang|technikraum|keller|heizzentrale|hz-raum|putzraum"):
        result.room = True

    def sentence_with(pattern):
        sentences = re.split(r"(?<=[;!])\s+|(?<=\.)\s+(?=[A-ZÄÖÜ*])", text)
        found = next((s for s in sentences if re.search(pattern, s, I)), "")
        return found.strip()[:140]

    result.key_hint = sentence_with(r"schl(ü|ue)ssel")
    result.announcement_hint = sentence_with(r"anmeld|terminbekanntgabe|terminaushang|termin (an|vorher)|im voraus|vorher informieren")
    result.units = sorted(units)
    result.reasons = list(dict.fromkeys(reasons))  # unique, order kept
    return result
