"""Compare Wiktionary redirect spellings with the complete observed corpus union."""
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from turkdict.common import read_json, write_json


def main():
    baseline = read_json(ROOT / 'build/macocu-baseline-latest.json')['outputs']
    db = sqlite3.connect(':memory:', uri=True)
    for name, path in [('defs',Path(baseline['normalize'])/'definitions.sqlite3'),
                       ('mac',ROOT/'data/macocu/frequency.sqlite3'),
                       ('freq',Path(baseline['frequency'])/'frequency.sqlite3')]:
        db.execute(f'ATTACH DATABASE ? AS {name}', (path.resolve().as_uri()+'?mode=ro',))
    start = time.perf_counter()
    db.execute('CREATE TABLE forms(surface TEXT PRIMARY KEY,macocu_count INTEGER,subtitle_count INTEGER) WITHOUT ROWID')
    db.execute('''INSERT INTO forms
        SELECT r.surface,coalesce(m.occurrences,0),coalesce(s.value,0)
        FROM (SELECT DISTINCT surface FROM defs.redirects) r
        LEFT JOIN mac.counts m ON m.surface=r.surface
        LEFT JOIN freq.signals s ON s.surface=r.surface AND s.source='subtitles' ''')
    row = db.execute('''SELECT count(*),sum(macocu_count>0),sum(subtitle_count>0),
        sum(macocu_count>0 AND subtitle_count>0),sum(macocu_count>0 OR subtitle_count>0),
        sum(macocu_count=0 AND subtitle_count=0) FROM forms''').fetchone()
    report = dict(zip(('wiktionary_redirect_spellings','observed_macocu','observed_subtitles','observed_both','observed_union','unobserved'),row))
    report['redirect_rows'] = db.execute('SELECT count(*) FROM defs.redirects').fetchone()[0]
    report['seconds'] = round(time.perf_counter()-start, 2)
    report['note'] = 'Exact normalized spelling overlap across the full corpora, not correctness or grammar validation. Unobserved Wiktionary redirects are excluded by the observed-only product policy.'
    write_json(ROOT / 'build/wiktionary-observed-audit.json', report)
    print(json.dumps(report))
    db.close()


if __name__ == '__main__':
    main()
