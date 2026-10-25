"""Separate analysis failures, missing definition links, and generated-only surfaces."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from turkdict.common import digest, key, read_json, write_json
from turkdict.frequency import ranked
from turkdict.morphology import create


def audit(db, words, mappings, analyze):
    """Count spellings, feature sequences, and source records separately."""
    observed = set(words)
    links = defaultdict(list)
    generated = set()
    pairs, sequences, source_pairs = set(), set(), set()
    rows = 0
    for m in mappings:
        if m['origin'] == 'generated':
            generated.add(m['surface'])
            continue
        if m['origin'] != 'observed' or m['surface'] not in observed:
            raise ValueError('Mapping is outside the selected observed forms')
        target = db.execute('SELECT key,language FROM entries WHERE id=?', (m['target_lemma_id'],)).fetchone()
        if target is None or target[1] != m['definition_language']:
            raise ValueError('Dangling or wrong-language mapping')
        lemma, language = target
        tags = tuple(f['id'] for f in m['features'])
        links[m['surface']].append((lemma, language, tags, m['evidence']))
        pairs.add((m['surface'], lemma))
        sequences.add((m['surface'], lemma, tags))
        source_pairs.add((m['surface'], m['target_lemma_id']))
        rows += 1
    report = dict(observed_forms=len(words), linked_observed_forms=len(links),
                  unlinked_without_analysis=0, unlinked_with_analysis=0,
                  analyzed_forms_with_some_unlinked_analyses=0,
                  no_analysis_but_exact_definition=0, no_analysis=0,
                  exact_definition_only_forms=0, ambiguous_engine_forms=0,
                  ambiguous_linked_engine_forms=0, multiple_linked_lemma_forms=0,
                  generated_only_surfaces=len(generated - observed),
                  generated_surfaces=len(generated), observed_mapping_records=rows,
                  observed_surface_definition_id_pairs=len(source_pairs),
                  observed_surface_lemma_spelling_pairs=len(pairs),
                  observed_distinct_feature_sequences=len(sequences),
                  languages={}, examples={})
    failures = []
    for language in ('tr', 'en'):
        report['languages'][language] = dict(linked_forms=0, unlinked_forms=0, exact_definition_only_forms=0)

    def example(category, row):
        examples = report['examples'].setdefault(category, [])
        if len(examples) < 15:
            examples.append(row)

    for rank, word in enumerate(words, 1):
        analyses = analyze(word)
        unique = {(key(a['lemma']), tuple(f['id'] for f in a['features'])) for a in analyses}
        linked = links[word]
        engine_links = {(lemma, tags) for lemma, _, tags, evidence in linked if evidence != 'dictionary-attested'}
        exact = any(evidence == 'dictionary-attested' for _, _, _, evidence in linked)
        report['no_analysis'] += not analyses
        report['ambiguous_engine_forms'] += len(unique) > 1
        report['ambiguous_linked_engine_forms'] += len(engine_links) > 1
        report['multiple_linked_lemma_forms'] += len({lemma for lemma, _, _, _ in linked}) > 1
        report['exact_definition_only_forms'] += exact and not engine_links
        report['no_analysis_but_exact_definition'] += exact and not analyses
        missing = sorted({lemma for lemma, _ in unique
                          if not db.execute('SELECT 1 FROM entries WHERE key=? LIMIT 1', (lemma,)).fetchone()})
        report['analyzed_forms_with_some_unlinked_analyses'] += bool(missing)
        row = dict(surface=word, rank=rank, lemmas=sorted({lemma for lemma, _ in unique}))
        for language, counts in report['languages'].items():
            localized = [entry for entry in linked if entry[1] == language]
            counts['linked_forms'] += bool(localized)
            counts['unlinked_forms'] += not localized
            counts['exact_definition_only_forms'] += bool(localized) and all(e[3] == 'dictionary-attested' for e in localized)
            if not localized:
                example('unlinked_' + language, row)
        if not linked:
            category = 'unlinked_with_analysis' if analyses else 'unlinked_without_analysis'
            report[category] += 1
            example(category, row)
            failures.append(dict(**row, category=category, analyses=analyses,
                                 review_status='unreviewed', reason=None))
        if exact and not engine_links:
            example('exact_definition_only', row)
        if len(engine_links) > 1:
            example('ambiguous_linked_engine', row)
    report['note'] = ('Type counts measure links, not correctness or token coverage. Lemma-spelling pairs collapse source IDs '
                      'and homographs; they are not reviewed lexical identities. Feature sequences include exact-headword '
                      'records and collapse source duplication; ambiguous candidates are not necessarily valid analyses. '
                      'All missing-link forms are retained for review; no spelling recovery is applied.')
    return report, failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--latest', type=Path, default=ROOT / 'build/latest.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/coverage-audit.json')
    parser.add_argument('--failures', type=Path, default=ROOT / 'build/coverage-failures.json')
    args = parser.parse_args()
    outputs = read_json(args.latest)['outputs']
    for stage in ('normalize', 'frequency', 'analyze'):
        directory = Path(outputs[stage])
        for name, expected in read_json(directory / 'receipt.json')['outputs'].items():
            if digest(directory / name) != expected:
                raise ValueError(f'Corrupt {stage} output: {name}')
    receipt = read_json(Path(outputs['analyze']) / 'receipt.json')['inputs']
    if receipt['definitions'] != Path(outputs['normalize']).name or receipt['frequency'] != Path(outputs['frequency']).name:
        raise ValueError('Analysis receipt refers to different input stages')
    for name, expected in receipt.get('engine_files', {}).items():
        if digest(ROOT / name) != expected:
            raise ValueError(f'Engine input changed since analysis: {name}')
    db = sqlite3.connect(Path(outputs['normalize']) / 'definitions.sqlite3')
    freq = sqlite3.connect(Path(outputs['frequency']) / 'frequency.sqlite3')
    engine = None
    try:
        engine = create(receipt['config']['engine'], ROOT)
        words = [word for word, _ in ranked(freq, receipt['weights']).fetchmany(receipt['config']['observed_limit'])]
        with (Path(outputs['analyze']) / 'mappings.jsonl').open(encoding='utf-8') as stream:
            report, failures = audit(db, words, (json.loads(line) for line in stream), engine.analyze)
        report['inputs'] = dict(analyze=Path(outputs['analyze']).name, engine=engine.identity,
                               mappings_sha256=digest(Path(outputs['analyze']) / 'mappings.jsonl'))
        args.failures.parent.mkdir(parents=True, exist_ok=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.failures, dict(inputs=report['inputs'], forms=failures))
        report['failure_review_file'] = str(args.failures.resolve())
        report['failure_review_sha256'] = digest(args.failures)
        write_json(args.output, report)
        print(json.dumps({k: v for k, v in report.items() if k != 'examples'}, ensure_ascii=True, indent=2))
    finally:
        if engine:
            engine.close()
        db.close()
        freq.close()


if __name__ == '__main__':
    main()
