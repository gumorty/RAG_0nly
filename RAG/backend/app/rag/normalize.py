import hashlib
import re
import unicodedata
from collections import Counter


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"(Authorization\s*:\s*Bearer\s+)[A-Za-z0-9._\-]+", r"\1<redacted>", text, flags=re.IGNORECASE)
    text = re.sub(r"(api[_-]?key\s*[:=]\s*)[A-Za-z0-9._\-]+", r"\1<redacted>", text, flags=re.IGNORECASE)
    text = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "sk-<redacted>", text)
    text = text.replace("\x00", "")
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\t\u00a0]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ ]{2,}", " ", text)
    return text.strip()


def tokenize_for_sparse(text: str) -> dict[str, int]:
    tokens: list[str] = []
    for token in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", text.lower()):
        tokens.append(token)
        if re.fullmatch(r"[\u4e00-\u9fff]+", token) and len(token) > 2:
            for size in (2, 3, 4):
                tokens.extend(token[index : index + size] for index in range(0, len(token) - size + 1))
    return dict(Counter(token for token in tokens if len(token) > 1))


def estimate_tokens(text: str) -> int:
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_words = len(re.findall(r"[A-Za-z0-9_]+", text))
    punctuation = len(re.findall(r"[^\w\s\u4e00-\u9fff]", text))
    return max(1, int(chinese_chars * 0.8 + latin_words * 1.2 + punctuation * 0.2))
