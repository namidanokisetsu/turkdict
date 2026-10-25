"""Run each engine in isolation; measurements never enter reproducible build artifacts."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from turkdict.common import digest, key, read_json, write_json
from turkdict.morphology import create


def worker(name, output):
    import psutil
    own = psutil.Process()
    peak = [0]
    stop = threading.Event()
    def sample():
        while not stop.wait(0.01):
            try:
                peak[0] = max(peak[0], sum(p.memory_info().rss for p in [own] + own.children(recursive=True)))
            except psutil.Error:
                pass
    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    data = read_json(ROOT / 'evaluation/cases.json')
    cases = {c['surface']: c for c in data['cases']}
    for correction in read_json(ROOT / 'evaluation/corrections.json')['corrections']:
        if not all(correction.get(k) for k in ('reviewer', 'date', 'reason')):
            raise ValueError('Correction lacks review provenance')
        cases[correction['surface']]['expected'] = correction['expected']
    start = time.perf_counter()
    engine = create(name, ROOT)
    initialization = time.perf_counter() - start
    rows = []
    lemma_hits = feature_hits = expected = negatives = false_negatives = 0
    start = time.perf_counter()
    try:
        for case in cases.values():
            actual = engine.analyze(case['surface'])
            missing_lemmas, missing_features = [], []
            for wanted in case['expected']:
                expected += 1
                same = [a for a in actual if a['lemma'] == key(wanted['lemma'])]
                lemma_hits += bool(same)
                feature_match = any(set(wanted['features']) <= {f['id'] for f in a['features']} for a in same)
                feature_hits += feature_match
                if not same:
                    missing_lemmas.append(wanted)
                if not feature_match:
                    missing_features.append(wanted)
            if case.get('negative'):
                negatives += 1
                false_negatives += bool(actual)
            rows.append(dict(**case, analyses=actual, missing_lemmas=missing_lemmas, missing_features=missing_features))
        elapsed = time.perf_counter() - start
        # Distinct corpus forms avoid overstating performance through repeated cache hits.
        tokens = []
        from turkdict.common import valid_token
        with (ROOT / 'data/raw/subtitles.txt').open(encoding='utf-8') as f:
            for line in f:
                token = line.rsplit(' ', 1)[0]
                if valid_token(token):
                    tokens.append(token)
                if len(tokens) == 2000:
                    break
        start = time.perf_counter()
        for token in tokens:
            engine.analyze(token)
        throughput_elapsed = time.perf_counter() - start
        report = dict(engine=engine.identity, review_status=data['review_status'], python=platform.python_version(),
                      cases_sha256=digest(ROOT / 'evaluation/cases.json'), corrections_sha256=digest(ROOT / 'evaluation/corrections.json'),
                      dependencies_sha256=digest(ROOT / 'requirements.lock'), java_lock_sha256=digest(ROOT / 'config/java.lock.json'),
                      initialization_seconds=initialization, evaluation_seconds=elapsed,
                      throughput=dict(forms=len(tokens), seconds=throughput_elapsed, forms_per_second=len(tokens)/throughput_elapsed),
                      expected_analyses=expected, lemma_hits=lemma_hits, feature_constraint_hits=feature_hits,
                      lemma_recall=lemma_hits/expected, feature_constraint_recall=feature_hits/expected,
                      missed_expected_analyses=expected-feature_hits, negative_controls=negatives,
                      negative_controls_with_false_matches=false_negatives,
                      precision=None, precision_note='Positive expectations are non-exhaustive; additional analyses are unjudged, not false positives.',
                      feature_note='Checks named feature subsets, not full sequence accuracy. Engine defaults/derivation conventions differ.',
                      cases=rows)
    finally:
        engine.close()
        stop.set()
        thread.join()
    report['peak_process_tree_rss_bytes'] = peak[0]
    write_json(output, report)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--worker', choices=('zemberek', 'dilbaz'))
    p.add_argument('--output', type=Path)
    args = p.parse_args()
    if args.worker:
        worker(args.worker, args.output)
        return
    directory = ROOT / 'build/evaluation'
    directory.mkdir(parents=True, exist_ok=True)
    for name in ('zemberek', 'dilbaz'):
        subprocess.run([sys.executable, __file__, '--worker', name, '--output', str(directory / f'{name}.json')], check=True)
        r = read_json(directory / f'{name}.json')
        print(name, json.dumps({k:v for k,v in r.items() if k != 'cases'}), flush=True)


if __name__ == '__main__':
    main()
