import re
import time
import math

def with_retry(fn, retries: int = 3, base_delay: float = 2.0):
    """Call fn(), retrying up to `retries` times with exponential back-off."""
    last_exc = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if attempt < retries - 1:
                time.sleep(base_delay * (2 ** attempt))
    raise last_exc


def split_long_paragraph(paragraph: str, max_words: int) -> list[str]:
    words = paragraph.split()
    if len(words) <= max_words:
        return [paragraph]

    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    chunks, current, current_wc = [], [], 0
    for sentence in sentences:
        sentence_words = sentence.split()
        if len(sentence_words) > max_words:
            if current:
                chunks.append(" ".join(current))
                current, current_wc = [], 0
            for i in range(0, len(sentence_words), max_words):
                chunks.append(" ".join(sentence_words[i:i + max_words]))
            continue
        if current and current_wc + len(sentence_words) > max_words:
            chunks.append(" ".join(current))
            current, current_wc = [sentence], len(sentence_words)
        else:
            current.append(sentence)
            current_wc += len(sentence_words)
    if current:
        chunks.append(" ".join(current))
    return chunks


def chunk_text(text: str, max_words: int = 4000) -> list[str]:
    """Split text into chunks at paragraph boundaries, targeting max_words per chunk."""
    if len(text.split()) <= max_words:
        return [text]
    paragraphs = []
    for p in (p for p in text.split('\n\n') if p.strip()):
        paragraphs.extend(split_long_paragraph(p, max_words))
    chunks, current, current_wc = [], [], 0
    for para in paragraphs:
        wc = len(para.split())
        if current and current_wc + wc > max_words:
            chunks.append('\n\n'.join(current))
            current, current_wc = [para], wc
        else:
            current.append(para)
            current_wc += wc
    if current:
        chunks.append('\n\n'.join(current))
    return chunks or [text]


def normalize_tag_name(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for fuzzy matching."""
    name = name.lower().strip()
    name = re.sub(r"[^\w\s]", "", name)
    name = re.sub(r"\s+", " ", name)
    return name


def cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors (OpenAI embeddings are unit-norm, so this is just dot product)."""
    return sum(x * y for x, y in zip(a, b))
