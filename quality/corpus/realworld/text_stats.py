"""Word statistics for a block of text."""
import re


def words(text):
    return [w.lower() for w in re.findall(r"[A-Za-z']+", text)]


def frequencies(text, stop=()):
    counts = {}
    for word in words(text):
        if word in stop:
            continue
        counts[word] = counts.get(word, 0) + 1
    return counts


def top(counts, n=3):
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:n]


def longest(text):
    best = ""
    for word in words(text):
        if len(word) > len(best):
            best = word
    return best


def summarize(text):
    counts = frequencies(text, stop=("the", "a", "of"))
    return {
        "unique": len(counts),
        "top": top(counts),
        "longest": longest(text),
        "sentences": len([s for s in re.split(r"[.!?]+", text) if s.strip()]),
    }


if __name__ == "__main__":
    sample = "The quick brown fox jumps over the lazy dog. A dog is a loyal friend! The fox is quick."
    for key, value in summarize(sample).items():
        print(key, value)
RESULT = longest("a bb ccc")
