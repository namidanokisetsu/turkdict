import hashlib
import json
import unicodedata
from pathlib import Path


def key(text):
    return unicodedata.normalize("NFC", text).translate(str.maketrans("Iİ’ʼ", "ıi''")).lower().strip()


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def identity(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def write_json(path, value):
    Path(path).write_text(dumps(value) + "\n", encoding="utf-8", newline="\n")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def valid_token(text):
    text = key(text)
    letters = set("abcçdefgğhıijklmnoöprsştuüvyzâîûqwx")
    pieces = text.split("'")
    return (0 < len(text) <= 64 and len(pieces) <= 2
            and all(p and all(c in letters for c in p) for p in pieces))
