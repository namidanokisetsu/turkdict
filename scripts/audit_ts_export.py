"""Audit a CQPweb frequency export, preserving embedded newlines in words."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re


def audit(path):
    raw = path.read_bytes()
    text = raw.decode('utf-8-sig')
    rows = [(int(m[1]), m[2], int(m[3])) for m in re.finditer(
        r'^(\d+)\t(.*?)\t(\d+)\r?$', text, re.MULTILINE | re.DOTALL)]
    footer = re.search(r'^Total:\t\t(\d+)\s*$', text, re.MULTILINE)
    total = int(footer[1]) if footer else None
    summed = sum(c for _, _, c in rows)
    return dict(
        file=path.name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
        header=text.splitlines()[0], utf8_valid=True, records=len(rows),
        contiguous_ranks=all(rank == i for i, (rank, _, _) in enumerate(rows, 1)),
        descending_counts=all(a[2] >= b[2] for a, b in zip(rows, rows[1:])),
        reported_total=total, computed_total=summed, total_matches=total == summed,
        frequency_bands=dict(Counter('1000+' if c >= 1000 else '100-999' if c >= 100
                                     else '10-99' if c >= 10 else '2-9' if c >= 2 else '1'
                                     for _, _, c in rows)),
        replacement_character_records=sum('\ufffd' in w for _, w, _ in rows),
        multiline_records=[dict(rank=n, word=w, frequency=c) for n, w, c in rows if len(w.splitlines()) > 1],
        first_10=[dict(rank=n, word=w, frequency=c) for n, w, c in rows[:10]],
        specific_spellings={word: [dict(rank=n, frequency=c) for n, w, c in rows if w == word]
                            for word in ('ama', 'ancak', 'arasında', 'Âma', 'ancâk', 'ArasInda')},
        interpretation='Complete-looking prefix export: sequential ranks and footer sum establish internal consistency, not independent proof of server-side coverage. Display spellings contain anomalies and cannot yet be treated as clean exact-form attestations.',
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path, nargs='?', default=Path('frequency_list.txt'))
    args = parser.parse_args()
    report = audit(args.file)
    destination = Path('build/tscorpus-a-export-audit.json')
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
