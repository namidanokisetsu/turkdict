"""Compare equal-weight subtitle/MaCoCu ranks with the retained 15k baseline."""
import csv
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from turkdict.common import read_json, write_json
from turkdict.frequency import ranked


def main():
    directory = ROOT / 'data/macocu'
    receipt = read_json(directory / 'report.json')
    if not receipt['complete'] or receipt['limited_documents']:
        raise ValueError('Requires the complete corpus extraction')
    latest = read_json(ROOT / 'build/macocu-baseline-latest.json')
    baseline_db = sqlite3.connect(Path(latest['outputs']['frequency']) / 'frequency.sqlite3')
    baseline = list(ranked(baseline_db, {'subtitles': 1, 'wordfreq': 1}).fetchmany(15000))
    subtitle = {w: (int(n), r) for w, n, r in baseline_db.execute("SELECT surface,value,rank FROM signals WHERE source='subtitles'")}
    reference = {w: r for w, r in baseline_db.execute("SELECT surface,rank FROM signals WHERE source='wordfreq'")}
    baseline_db.close()
    corpus = sqlite3.connect(directory / 'frequency.sqlite3')
    # Any top-N fused candidate must be top-(2N+60) in at least one of
    # two equally weighted sources with reciprocal-rank offset 60.
    # Retain all source counts to make the comparison directly inspectable.
    cutoff = 30060
    candidates = {w for w, (_, r) in subtitle.items() if r <= cutoff}
    baseline_words = {w for w, _ in baseline}
    macocu = {}
    for rank, (word, count) in enumerate(corpus.execute('SELECT surface,occurrences FROM counts ORDER BY occurrences DESC,surface'), 1):
        if rank <= cutoff or word in candidates or word in baseline_words:
            macocu[word] = (count, rank)
        if rank <= cutoff:
            candidates.add(word)
    corpus.close()
    def score(word):
        return sum(1 / (60 + source[word][1]) for source in (subtitle, macocu) if word in source)
    selected = sorted(candidates, key=lambda w: (-score(w), w))[:15000]
    old_rank = {w: r for r, (w, _) in enumerate(baseline, 1)}
    new_rank = {w: r for r, w in enumerate(selected, 1)}
    changes = ROOT / 'build/macocu-ranking-changes.tsv'
    with changes.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream, delimiter='\t')
        writer.writerow(['surface', 'status', 'baseline_rank', 'combined_rank', 'subtitle_count', 'subtitle_rank', 'macocu_count', 'macocu_rank', 'wordfreq_rank'])
        for word in sorted(set(old_rank) | set(new_rank), key=lambda w: (new_rank.get(w, 999999), old_rank.get(w, 999999))):
            status = 'retained' if word in old_rank and word in new_rank else 'entered' if word in new_rank else 'left'
            writer.writerow([word, status, old_rank.get(word, ''), new_rank.get(word, ''), *subtitle.get(word, ('', '')), *macocu.get(word, ('', '')), reference.get(word, '')])
    failures = read_json(ROOT / 'build/macocu-baseline-failures.json')['forms']
    summary = dict(limit=15000, baseline_weights={'subtitles': 1, 'wordfreq': 1}, proposed_weights={'subtitles': 1, 'macocu': 1},
                   fusion='sum of 1/(60+source_rank); deterministic spelling tie-break',
                   retained=len(set(old_rank) & set(new_rank)), entered=len(set(new_rank)-set(old_rank)), left=len(set(old_rank)-set(new_rank)),
                   entered_examples=[w for w in selected if w not in old_rank][:50],
                   left_examples=[w for w, _ in baseline if w not in new_rank][:50],
                   baseline_missing_links_retained=sum(r['surface'] in new_rank for r in failures),
                   extraction=receipt, baseline_build=latest, changes_file=str(changes),
                   note='Ranking comparison only. New forms have not been morphology-analyzed; baseline correctness is unreviewed. Wordfreq is reference-only. MaCoCu quality labels are not proof of clean Turkish.')
    write_json(ROOT / 'build/macocu-ranking-comparison.json', summary)
    print(json.dumps({k: v for k, v in summary.items() if k not in ('extraction', 'baseline_build')}, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
