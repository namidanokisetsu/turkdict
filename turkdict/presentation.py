"""Compact display labels; canonical morphology evidence stays unchanged."""
import re


SHORT_LABELS = {
    'his / her / its (possessive)': 'his/her/its (poss.)',
    'your (plural possessive)': 'your (pl. poss.)',
    'my (possessive)': 'my (poss.)', 'your (possessive)': 'your (poss.)',
    'our (possessive)': 'our (poss.)', 'their (possessive)': 'their (poss.)',
    'he / she / it': 'he/she/it', 'you (plural)': 'you (pl.)',
    'accusative (direct object)': 'accusative', 'dative (to / toward)': 'dative',
    'locative (in / at)': 'locative', 'ablative (from)': 'ablative',
    'genitive (of)': 'genitive', 'instrumental (with / by)': 'instrumental',
    'aorist (general / habitual)': 'aorist', 'conditional (if)': 'conditional',
    'causative (make / have)': 'causative', 'necessity (must)': 'necessitative',
    'inability (cannot)': 'inability', 'ability (can)': 'ability',
    '-DIK clause / participle': '-DIK participle/clause',
    'no possessive': 'no poss.',
    'first-person singular': '1st person sg.', 'second-person singular': '2nd person sg.',
    'third-person singular': '3rd person sg.', 'first-person plural': '1st person pl.',
    'second-person plural': '2nd person pl.', 'third-person plural': '3rd person pl.',
    'singular': 'sg.', 'plural': 'pl.',
    'adjective': 'adj.', 'adverb': 'adv.', 'pronoun': 'pron.',
    'possessive': 'poss.',
}
_LABEL_PATTERN = re.compile(r'(?<!\w)(?:' + '|'.join(
    re.escape(label) for label in sorted(SHORT_LABELS, key=len, reverse=True)) + r')(?!\w)')


def compact_label(label):
    return _LABEL_PATTERN.sub(lambda match: SHORT_LABELS[match[0]], label)


def factor_paths(paths):
    """Factor only common ends, keeping correlated alternatives as whole paths."""
    paths = [list(path) for path in paths]
    if not paths:
        return [], [], []
    prefix, suffix = [], []
    while all(paths) and len({path[0] for path in paths}) == 1:
        prefix.append(paths[0][0])
        for path in paths:
            path.pop(0)
    while all(paths) and len({path[-1] for path in paths}) == 1:
        suffix.insert(0, paths[0][-1])
        for path in paths:
            path.pop()
    return prefix, paths, suffix


def compact_chain(paths):
    prefix, middles, suffix = factor_paths(paths)
    labels = prefix[:]
    if len(middles) > 1:
        labels.append('(' + ' or '.join(' · '.join(path) or 'unmarked' for path in middles) + ')')
    elif middles:
        labels.extend(middles[0])
    return ' · '.join(labels + suffix)
