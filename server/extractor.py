import base64
import json
import os
import re
from typing import Dict, List, Optional

ENGINE = os.getenv("OCR_ENGINE", "ocr").lower()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3-vl:2b")

COUNTIES = set(
    "AB AR AG BC BH BN BT BV BR BZ CS CL CJ CT CV DB DJ GL GR GJ HR HD IL IS IF "
    "MM MH MS NT OT PH SM SJ SB SV TR TM TL VS VL VN".split()
)
VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
TO_DIGIT = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8", "G": "6"})
TO_LETTER = str.maketrans({"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B", "6": "G"})


def clean_vin(raw: str) -> str:
    vin = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
    return vin.translate(str.maketrans({"I": "1", "O": "0", "Q": "0"}))


def parse_plate(raw: str) -> Optional[str]:
    p = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
    if not 6 <= len(p) <= 8:
        return None
    for county_len, digits in ((2, 2), (1, 3), (1, 2)):
        if len(p) != county_len + digits + 3:
            continue
        county = p[:county_len].translate(TO_LETTER)
        num = p[county_len:county_len + digits].translate(TO_DIGIT)
        letters = p[county_len + digits:].translate(TO_LETTER)
        if not (num.isdigit() and letters.isalpha()):
            continue
        if county_len == 1 and county == "B":
            return f"B {num} {letters}"
        if county_len == 2 and county in COUNTIES and digits == 2:
            return f"{county} {num} {letters}"
    m = re.fullmatch(r"([A-Z]{1,2})(\d{6})", p)
    if m and (m.group(1) == "B" or m.group(1) in COUNTIES):
        return " ".join(m.groups())
    return None


def is_romanian_plate(plate: str) -> bool:
    return parse_plate(plate) == plate


def finish(owner: str, vin: str, plate: str, unreadable=()) -> Dict:
    owner = " ".join(owner.split())
    vin = clean_vin(vin)
    plate = parse_plate(plate) or plate.strip().upper()
    warnings = {}
    for key, value, label in (("owner_name", owner, "owner"), ("vin", vin, "VIN"), ("license_plate", plate, "plate")):
        if key in unreadable or not value:
            warnings[key] = f"Couldn't read the {label}. Type it in."
    if vin and "vin" not in warnings and not VIN_RE.match(vin):
        warnings["vin"] = f"VIN has {len(vin)} characters instead of 17. Check it against the paper."
    if plate and "license_plate" not in warnings and not is_romanian_plate(plate):
        warnings["license_plate"] = "Not a standard Romanian plate format. Check it."
    if owner and "owner_name" not in warnings and " " not in owner and len(owner) > 12:
        warnings["owner_name"] = "Spaces may be missing from the name. Check it."
    return {"fields": {"owner_name": owner, "vin": vin, "license_plate": plate}, "warnings": warnings, "notice": ""}


def failed(message: str) -> Dict:
    return {"fields": {"owner_name": "", "vin": "", "license_plate": ""}, "warnings": {}, "notice": message}


_ocr = None


def _engine():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR

        _ocr = RapidOCR()
    return _ocr


class Box:
    def __init__(self, pts, text: str):
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        self.x0, self.x1, self.y0, self.y1 = min(xs), max(xs), min(ys), max(ys)
        self.cy, self.h = (self.y0 + self.y1) / 2, self.y1 - self.y0
        self.text = text.strip()
        self.compact = re.sub(r"[^A-Za-z0-9]", "", self.text).upper()


LABEL_RE = re.compile(r"^\(?([A-Z])\s*\.?\s*(\d)?\s*\.?\s*(\d)?\)?\s*[:.]?\s*(.*)$", re.I)


def _split_label(text: str):
    m = LABEL_RE.match(text.strip())
    if not m:
        return None, text
    letter, d1, d2, rest = m.groups()
    code = letter.upper() + (d1 or "") + (d2 or "")
    known = {"A", "E", "C11", "C12", "C13", "C21", "C22"}
    if code not in known:
        return None, text
    if rest and not re.match(r"^\(?[A-Za-z]\s*\.?\s*\d?\s*\.?\s*\d?\)?\s*[:.\s]", text.strip()):
        return None, text
    return code, rest.strip()


def _value_right_of(label: Box, boxes: List[Box]) -> str:
    same_line = [
        b for b in boxes
        if b is not label and label.x1 - 5 < b.x0 < label.x1 + label.h * 12
        and abs(b.cy - label.cy) < max(label.h, b.h) * 0.7
    ]
    same_line.sort(key=lambda b: b.x0)
    return same_line[0].text if same_line else ""


def _fields_from_boxes(boxes: List[Box]) -> Dict[str, str]:
    labelled: Dict[str, str] = {}
    for b in boxes:
        code, inline = _split_label(b.text)
        if not code or code in labelled:
            continue
        value = inline or _value_right_of(b, boxes)
        if _split_label(value)[0]:
            value = ""
        labelled[code] = value
    return labelled


def _find_vin(labelled, boxes) -> str:
    candidates = [labelled.get("E", "")] + [b.text for b in boxes]
    for c in candidates:
        v = clean_vin(c)
        if VIN_RE.match(v) and re.search(r"\d", v) and re.search(r"[A-Z]", v):
            return v
    return clean_vin(labelled.get("E", ""))


def _find_plate(labelled, boxes) -> str:
    for c in [labelled.get("A", "")] + [b.text for b in boxes]:
        p = parse_plate(c)
        if p:
            return p
    return labelled.get("A", "")


def _find_owner(labelled) -> str:
    c21 = labelled.get("C21", "")
    if c21 and not re.search(r"VEZI|C\.?\s*1", c21, re.I):
        return f"{c21} {labelled.get('C22', '')}"
    return f"{labelled.get('C11', '')} {labelled.get('C12', '')}"


def extract_with_ocr(jpeg: bytes) -> Dict:
    try:
        result, _ = _engine()(jpeg)
    except Exception as exc:
        return failed(f"Couldn't read the document ({type(exc).__name__}). Type the fields in.")
    if not result:
        return failed("No text found in the photo. Retake it closer, flat and well lit.")
    boxes = [Box(pts, txt) for pts, txt, _score in result]
    labelled = _fields_from_boxes(boxes)
    return finish(_find_owner(labelled), _find_vin(labelled, boxes), _find_plate(labelled, boxes))


PROMPT = """This is a photo of a vehicle registration document, usually the Romanian
"certificat de înmatriculare". Read these fields exactly as printed:
- license_plate: field A
- vin: field E (17 characters)
- owner_name: C.2.1 + C.2.2 if filled in, otherwise C.1.1 + C.1.2
Use an empty string for any field you cannot read clearly. Never guess."""

SCHEMA = {
    "type": "object",
    "properties": {"owner_name": {"type": "string"}, "vin": {"type": "string"}, "license_plate": {"type": "string"}},
    "required": ["owner_name", "vin", "license_plate"],
}


def extract_with_ollama(jpeg: bytes) -> Dict:
    import httpx

    try:
        r = httpx.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "stream": False,
                "format": SCHEMA,
                "options": {"temperature": 0},
                "messages": [{"role": "user", "content": PROMPT, "images": [base64.b64encode(jpeg).decode()]}],
            },
            timeout=300,
        )
        r.raise_for_status()
        data = json.loads(r.json()["message"]["content"])
    except Exception as exc:
        return failed(f"The local AI model didn't answer ({type(exc).__name__}). Is Ollama running? Type the fields in.")
    return finish(str(data.get("owner_name", "")), str(data.get("vin", "")), str(data.get("license_plate", "")))


def extract_fields(jpeg: bytes) -> Dict:
    return extract_with_ollama(jpeg) if ENGINE == "ollama" else extract_with_ocr(jpeg)


def warm_up() -> None:
    if ENGINE != "ollama":
        try:
            _engine()
        except Exception:
            pass
