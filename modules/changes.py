"""What changed between two reports of the same series.

Reports are rewritten from scratch every day, so a line-by-line diff (or
even a sentence similarity) finds nothing in common. Instead each report is
cut into points (bullets and paragraph lines, with the section they sit
under), and a point continues from the other report when at least half of
its key words appear in one of that report's points. On China 20 -> 21 Oct
2026 that marked 3 of 25 points as continuing.
"""
import re
from typing import Dict, FrozenSet, List, Tuple

SAME = 0.5  # share of key words two points must have in common
_STOP = frozenset(
    'this that with from have will been were their they them which while would could '
    'should about after before into over under also more most than then there these '
    'those what when where amid other such only some very just said says year years '
    'week weeks month today yesterday daily brief report reports source sources '
    'confidence medium high low'.split()
)
_MIN_CHARS = 25  # shorter points ("---", "Sources:") aren't worth comparing
_BULLET = re.compile(r'^\s*(?:[-*+]|\d+[.)])\s+(.*)$')
_HEADING = re.compile(r'^#{1,6}\s+(.+?)\s*#*\s*$')


def points(markdown_text: str) -> List[Tuple[str, str]]:
    """(section, text) for each bullet or paragraph worth comparing.

    A line straight after a bullet or paragraph line (no blank line between)
    belongs to it, as in Markdown, so a bullet's "Confidence: High" line is
    part of that bullet rather than a point of its own.
    """
    section = ''
    found = []
    current = None  # [section, text] of the point being read

    def finish():
        if current and len(current[1]) >= _MIN_CHARS:
            found.append((current[0], current[1]))

    for line in markdown_text.split('\n'):
        heading = _HEADING.match(line)
        bullet = _BULLET.match(line)
        if heading or bullet or not line.strip() or line.lstrip().startswith('|'):
            finish()
            current = None
        if heading:
            section = _plain(heading[1])
        elif bullet:
            current = [section, _plain(bullet[1])]
        elif line.strip() and not line.lstrip().startswith('|'):
            text = _plain(line)
            if current:
                current[1] = f'{current[1]} {text}'.strip()
            else:
                current = [section, text]
    finish()
    return found


def _plain(text: str) -> str:
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)  # links -> their text
    text = re.sub(r'[*_`>]+', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def _key(text: str) -> FrozenSet[str]:
    """Key words: numbers and words of 4+ letters that aren't filler"""
    return frozenset(
        word for word in re.findall(r'[a-z0-9]+', text.lower())
        if (len(word) >= 4 or word.isdigit()) and word not in _STOP
    )


def _best(words: FrozenSet[str], others: List[FrozenSet[str]]) -> float:
    """Highest share of the smaller point's key words found in one of `others`"""
    best = 0.0
    for other in others:
        if words and other:
            best = max(best, len(words & other) / min(len(words), len(other)))
    return best


def compare(old_text: str, new_text: str) -> Dict:
    """Points new in `new_text`, points it carries over, and points of
    `old_text` no longer there, each grouped by section in report order"""
    old = points(old_text)
    new = points(new_text)
    old_keys = [_key(text) for _, text in old]
    new_keys = [_key(text) for _, text in new]
    added = [p for p, k in zip(new, new_keys) if _best(k, old_keys) < SAME]
    kept = [p for p, k in zip(new, new_keys) if _best(k, old_keys) >= SAME]
    dropped = [p for p, k in zip(old, old_keys) if _best(k, new_keys) < SAME]
    return {
        'added': _by_section(added),
        'kept': _by_section(kept),
        'dropped': _by_section(dropped),
        'counts': {
            'added': len(added),
            'dropped': len(dropped),
            'kept': len(new) - len(added),
            'new_total': len(new),
        },
    }


def _by_section(found: List[Tuple[str, str]]) -> List[Tuple[str, List[str]]]:
    grouped: List[Tuple[str, List[str]]] = []
    for section, text in found:
        if grouped and grouped[-1][0] == section:
            grouped[-1][1].append(text)
        else:
            grouped.append((section, [text]))
    return grouped
