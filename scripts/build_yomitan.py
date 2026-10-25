"""Resumable observed-corpus Yomitan build. Never labels unchecked packs final."""
import argparse
from collections import Counter
import json
import inspect
from pathlib import Path
import re
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from turkdict.common import digest, dumps, identity, read_json, valid_token, write_json
from turkdict.morphology import Zemberek
from scripts.export_prototype import main as export


def read_only(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def noise(surface):
    if not valid_token(surface):
        return 'malformed'
    if surface in {'www', 'http', 'https', 'html', 'htm', 'php', 'com', 'jpg', 'jpeg', 'png', 'nbsp'}:
        return 'web-token'
    return None


def grammar_labels(raw):
    """Accept explicit grammatical evidence, never typo/case/alternative-form redirects."""
    labels = raw[1] if len(raw) == 2 else []
    if isinstance(labels, str):
        labels = [labels]
    vocabulary = set(('first second third person singular plural accusative definite indefinite '
                      'nominative dative locative ablative genitive instrumental possessive '
                      'past present future aorist progressive inferential necessitative continuative '
                      'conditional optative imperative participle participial converb gerund '
                      'negative positive indicative perfect imperfect perfective temporal adverb infinitive verbal noun '
                      'of and or the form tense mood active passive potential ability possessed single many '
                      'personal impersonal prospective non from possession predicative simple multiple '
                      'continuous causative emphatic polite diminutive intensive comparative superlative '
                      'subject uncountable feminine masculine transitive intransitive degree evidential '
                      'reflexive adverbial simultaneous imperfective').split())
    grammatical = {'accusative', 'nominative', 'dative', 'locative', 'ablative', 'genitive',
                   'instrumental', 'possessive', 'plural', 'past', 'present', 'future', 'aorist',
                   'progressive', 'inferential', 'necessitative', 'continuative', 'conditional',
                   'optative', 'imperative', 'participle', 'participial', 'converb', 'gerund', 'adverb',
                   'person','singular','noun','causative','passive','reflexive','comparative','superlative'}
    if not labels or not all(isinstance(label, str) for label in labels):
        return None
    words = set(re.findall('[a-z]+', ' '.join(labels).lower()))
    return labels if words <= vocabulary and words & grammatical else None


def variant_labels(raw):
    """Retain explicit source-attested variants with their restrictions visible."""
    labels = raw[1] if len(raw) == 2 else []
    if isinstance(labels, str):
        labels = [labels]
    if not labels or not all(isinstance(label, str) for label in labels):
        return None
    words = set(re.findall('[a-z]+', ' '.join(labels).lower()))
    vocabulary = set(('alternative colloquial proscribed misspelling archaic obsolete rare dated nonstandard '
                      'spelling form of standard orthographic informal dialectal historical regional variant').split())
    return labels if words <= vocabulary and words & {'alternative','misspelling','nonstandard','variant'} else None


def source_redirect_labels(raw):
    """Source-attested direct aliases need not fit our grammatical vocabulary.

    Classification affects reporting, not eligibility. Case-only aliases are
    handled by normalization rather than treated as morphology.
    """
    if len(raw) != 2 or not isinstance(raw[0],str):
        return None
    labels = [raw[1]] if isinstance(raw[1],str) else raw[1]
    if not isinstance(labels,list) or not all(isinstance(x,str) and x.strip() for x in labels):
        return None
    if labels and set(labels) <= {'uppercase','lowercase'}:
        return None
    return labels or ['form']


def candidates(out, baseline, limit):
    receipt = read_json(ROOT / 'data/macocu/report.json')
    if not receipt.get('complete') or receipt.get('limited_documents'):
        raise ValueError('Complete MaCoCu extraction required')
    mac_path = ROOT / 'data/macocu/frequency.sqlite3'
    freq_path = Path(baseline['frequency']) / 'frequency.sqlite3'
    stamp = dict(macocu=digest(mac_path), extraction=digest(ROOT / 'data/macocu/report.json'),
                 frequency=digest(freq_path), limit=limit, policy='observed-rank-fusion-v1')
    path = out / 'corpus.sqlite3'
    stamp_path = out / 'selection-inputs.json'
    if path.exists():
        if not stamp_path.exists() or read_json(stamp_path) != stamp:
            raise ValueError('Build inputs changed; use a fresh --output directory')
        db = sqlite3.connect(path)
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='selection_complete'").fetchone():
            return db
        db.close()
        raise ValueError('Incomplete selection; use a fresh output directory')
    write_json(stamp_path, stamp)
    db = sqlite3.connect(path)
    db.executescript('''CREATE TABLE candidates(surface TEXT PRIMARY KEY, subtitle_count INTEGER,
        subtitle_rank INTEGER, macocu_count INTEGER, macocu_rank INTEGER, wordfreq_rank INTEGER,
        score REAL, rank INTEGER);
        CREATE TABLE analyses(surface TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE redirects(surface TEXT,target TEXT,features TEXT,provenance TEXT,
            PRIMARY KEY(surface,target,features));''')
    freq = read_only(freq_path)
    subtitles = {w: (int(n), r) for w, n, r in freq.execute("SELECT surface,value,rank FROM signals WHERE source='subtitles'")}
    reference = dict(freq.execute("SELECT surface,rank FROM signals WHERE source='wordfreq'"))
    freq.close()
    mac = read_only(mac_path)
    # With two equally weighted sources, every top-N fused form lies in the
    # top-(2N+60) of at least one source, before the explicit noise exclusions.
    cutoff = 2 * (limit + 100) + 60
    selected = {w for w, (_, r) in subtitles.items() if r <= cutoff}
    mac_selected = {}
    overlap = total = token_total = 0
    for rank, (word, count) in enumerate(mac.execute('SELECT surface,occurrences FROM counts ORDER BY occurrences DESC,surface'), 1):
        total += 1
        token_total += count
        overlap += word in subtitles
        if rank <= cutoff or word in selected:
            mac_selected[word] = (count, rank)
        if rank <= cutoff:
            selected.add(word)
    mac.close()
    rejected = Counter()
    scored = []
    for word in selected:
        reason = noise(word)
        if reason:
            rejected[reason] += 1
            continue
        sc, sr = subtitles.get(word, (0, None))
        mc, mr = mac_selected.get(word, (0, None))
        score = (1 / (60 + sr) if sr else 0) + (1 / (60 + mr) if mr else 0)
        scored.append((word, sc, sr, mc, mr, reference.get(word), score))
    scored.sort(key=lambda row: (-row[-1], row[0]))
    db.executemany('INSERT INTO candidates VALUES(?,?,?,?,?,?,?,?)', (row + (rank,) for rank, row in enumerate(scored[:limit], 1)))
    db.execute('CREATE INDEX candidate_rank ON candidates(rank)')
    db.execute('CREATE TABLE selection_complete(ok INTEGER)')
    db.execute('INSERT INTO selection_complete VALUES(1)')
    db.commit()
    write_json(out / 'inventory.json', dict(macocu_forms=total, subtitle_forms=len(subtitles),
        overlap_forms=overlap, union_forms=total+len(subtitles)-overlap,
        macocu_occurrences=token_total, subtitle_occurrences=sum(n for n, _ in subtitles.values()),
        selected_forms=min(limit, len(scored)), rejected_candidates=dict(rejected),
        source_rank_window=cutoff, weights={'macocu': 1, 'subtitles': 1}, wordfreq='reference-only',
        note='Union is normalized observed spellings, not validated words. Coverage below is in-sample, not held-out correctness.'))
    return db


def analyze(db, defs, out):
    stamp = {str(p.relative_to(ROOT)): digest(p) for p in [ROOT / 'turkdict/morphology.py',
        ROOT / 'scripts/MorphologyBridge.java', ROOT / 'config/java.lock.json']}
    old = out / 'analysis-inputs.json'
    # Raw engine results do not depend on ranking, linking or export code.
    previous = {k:v for k,v in read_json(old).items() if Path(k).as_posix() != 'scripts/build_yomitan.py'} if old.exists() else stamp
    if previous != stamp:
        raise ValueError('Analysis code changed; use a fresh output directory')
    write_json(old, stamp)
    total = db.execute('SELECT count(*) FROM candidates WHERE surface NOT IN (SELECT surface FROM analyses)').fetchone()[0]
    rows = db.execute('SELECT surface FROM candidates WHERE surface NOT IN (SELECT surface FROM analyses) ORDER BY rank')
    if total:
        engine = Zemberek(ROOT)
        start = time.perf_counter()
        try:
            for i, (word,) in enumerate(rows, 1):
                db.execute('INSERT INTO analyses VALUES(?,?)', (word, dumps(engine.analyze(word))))
                if i % 5000 == 0:
                    db.commit()
                    if i % 50000 == 0 or i == total:
                        print(f'Analyzed {i:,}/{total:,} new forms ({i/(time.perf_counter()-start):.0f}/s)', flush=True)
            db.commit()
        finally:
            engine.close()
    redirect_stamp = identity([inspect.getsource(grammar_labels), inspect.getsource(variant_labels), inspect.getsource(source_redirect_labels)])
    redirect_receipt = out / 'redirect-inputs.json'
    if not redirect_receipt.exists() or read_json(redirect_receipt).get('policy_sha256') != redirect_stamp:
        # The source contains millions of redirects; stream once and retain
        # only selected, naturally observed spellings with explicit grammar.
        size = db.execute('SELECT count(*) FROM candidates').fetchone()[0]
        selected = {r[0] for r in db.execute('SELECT surface FROM candidates')} if size <= 2000000 else None
        db.execute('DELETE FROM redirects')
        accepted = rejected = 0
        for surface, target, raw, provenance in defs.execute('SELECT surface,target,features,provenance FROM redirects'):
            observed = surface in selected if selected is not None else db.execute('SELECT 1 FROM candidates WHERE surface=?',(surface,)).fetchone() is not None
            if not observed or surface == target:
                continue
            source_raw = json.loads(raw)['raw']
            labels = source_redirect_labels(source_raw)
            if labels:
                db.execute('INSERT OR IGNORE INTO redirects VALUES(?,?,?,?)', (surface, target, dumps(labels), provenance))
                accepted += 1
            else:
                rejected += 1
        db.commit()
        write_json(redirect_receipt, dict(accepted_rows=accepted, rejected_non_grammatical_rows=rejected, policy_sha256=redirect_stamp))


def mappings(db, defs, out, limit):
    directory = out / f'sample-{limit}'
    directory.mkdir(exist_ok=True)
    targets = {}
    # A compact lexical index avoids millions of repeated SQLite misses and
    # avoids retaining every unresolved corpus spelling in a negative cache.
    print('Loading definition target index', flush=True)
    for lemma, eid, language in defs.execute('SELECT key,min(id),language FROM entries GROUP BY key,language ORDER BY key,language'):
        targets.setdefault(lemma, []).append((eid, language))
    def target(lemma):
        return targets.get(lemma, ())
    redirects = {}
    feature_strings = {}
    for surface, lemma, labels, provenance in db.execute('SELECT surface,target,features,provenance FROM redirects'):
        if target(lemma):
            redirects.setdefault(surface, []).append((sys.intern(lemma), feature_strings.setdefault(labels, labels), provenance))
    for alternatives in redirects.values():
        alternatives.sort(key=lambda row: (row[0],row[1]))
    counts = Counter()
    examples = {}
    occurrences = Counter()
    with (directory / 'mappings.jsonl').open('w', encoding='utf-8', newline='\n') as stream:
        for surface, sc, mc, payload in db.execute('SELECT c.surface,subtitle_count,macocu_count,payload FROM candidates c JOIN analyses a USING(surface) WHERE rank<=? ORDER BY rank', (limit,)):
            analyses = json.loads(payload)
            counts['observed_forms'] += 1
            counts['without_engine_analysis'] += not analyses
            counts['engine_ambiguous_forms'] += len(analyses) > 1
            if target(surface):
                analyses.append(dict(lemma=surface, features=[dict(id='Lexical', label='dictionary headword')], raw='exact'))
            linked = [a for a in analyses if target(a['lemma'])]
            linked_lemmas = {a['lemma'] for a in linked}
            # Supplement missing targets only: keep engine alternatives and
            # avoid duplicate source paraphrases of the same morphology.
            for lemma, labels, provenance in redirects.get(surface, ()):
                if lemma in linked_lemmas or not target(lemma):
                    continue
                source_labels = json.loads(labels)
                is_grammar = grammar_labels([lemma, source_labels]) is not None
                linked.append(dict(lemma=lemma, features=[dict(id='WiktionaryGrammar' if is_grammar else 'WiktionaryRedirect', label=l) for l in source_labels],
                                   raw='wiktionary grammatical redirect' if is_grammar else 'wiktionary source redirect',
                                   source_provenance=json.loads(provenance)))
            distinct = {}
            for a in linked:
                distinct[(a['lemma'], dumps(a['features']))] = a
            linked = list(distinct.values())
            counts['linked_forms'] += bool(linked)
            counts['surface_lemma_pairs'] += len({a['lemma'] for a in linked})
            counts['grammatical_analyses'] += sum(a['raw'] not in ('exact','wiktionary source redirect') for a in linked)
            counts['source_attested_other_links'] += sum(a['raw'] == 'wiktionary source redirect' for a in linked)
            counts['analysis_sequences_including_lexical'] += len(linked)
            counts['wiktionary_supplemented_forms'] += any('source_provenance' in a for a in linked)
            counts['exact_only_forms'] += bool(linked) and all(a['raw'] == 'exact' for a in linked)
            category = 'linked' if linked else 'no_definition' if analyses else 'no_analysis'
            counts[category + '_forms'] += category != 'linked'
            if category != 'linked' and len(examples.setdefault(category, [])) < 30:
                examples[category].append(dict(surface=surface, engine_lemmas=[a['lemma'] for a in analyses]))
            for lang in ('tr', 'en'):
                hit = any(any(l == lang for _, l in target(a['lemma'])) for a in linked)
                counts['linked_' + lang] += hit
                occurrences['subtitle_' + lang] += sc * hit
                occurrences['macocu_' + lang] += mc * hit
            for a in linked:
                for eid, lang in target(a['lemma']):
                    record = dict(surface=surface, target_lemma_id=eid, definition_language=lang,
                        features=a['features'], origin='observed',
                        evidence='dictionary-attested' if a['raw'] == 'exact' else 'wiktionary-source-redirect' if a['raw'] == 'wiktionary source redirect' else 'wiktionary-grammar' if 'source_provenance' in a else 'engine-analysis',
                        provenance=dict(engine='zemberek:0.17.1', raw=a['raw'], analysis_lemma=a['lemma'],
                                        **({'source': a['source_provenance']} if 'source_provenance' in a else {})))
                    stream.write(dumps(record) + '\n')
            if counts['observed_forms'] % 100000 == 0:
                print(f"Linked {counts['linked_forms']:,} of {counts['observed_forms']:,} processed spellings", flush=True)
    inventory = read_json(out / 'inventory.json')
    coverage = {source: {lang: occurrences[source+'_'+lang] / inventory[source+'_occurrences'] for lang in ('tr','en')}
                for source in ('subtitle','macocu')}
    report = dict(counts=counts, corpus_token_link_coverage=coverage, examples=examples,
                  generated_forms=0, selection_limit=limit, counts_note='Pairs collapse source IDs and languages. Grammatical analyses exclude exact lexical records; analysis_sequences_including_lexical includes them. Links are not correctness judgments.')
    write_json(directory / 'coverage.json', report)
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/yomitan-corpus')
    parser.add_argument('--limits', type=int, nargs='+', default=[50000, 150000, 500000])
    parser.add_argument('--export-limit', type=int, help='Export one measured sample; omitted runs selection/analysis/coverage only')
    parser.add_argument('--reuse-analysis', type=Path, help='Reuse authenticated raw engine analyses from an earlier sample')
    args = parser.parse_args()
    if any(n <= 0 for n in args.limits):
        parser.error('Limits must be positive')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    baseline = read_json(ROOT / 'build/macocu-baseline-latest.json')['outputs']
    # Retained stage receipts authenticate the normalized pinned inputs.
    for stage, filename in [('normalize','definitions.sqlite3'), ('frequency','frequency.sqlite3')]:
        folder = Path(baseline[stage])
        if digest(folder / filename) != read_json(folder / 'receipt.json')['outputs'][filename]:
            raise ValueError('Retained baseline stage checksum mismatch: ' + stage)
    print('Auditing complete observed union and selecting candidates', flush=True)
    db = candidates(out, baseline, max(args.limits))
    if args.reuse_analysis:
        prior = args.reuse_analysis.resolve()
        prior_selection = read_json(prior / 'selection-inputs.json')
        selection = read_json(out / 'selection-inputs.json')
        if any(prior_selection[k] != selection[k] for k in ('macocu','extraction','frequency','policy')):
            raise ValueError('Cannot reuse analysis from different corpus inputs')
        prior_engine = read_json(prior / 'analysis-inputs.json')
        if any(digest(ROOT / name) != value for name, value in prior_engine.items() if Path(name).as_posix() != 'scripts/build_yomitan.py'):
            raise ValueError('Cannot reuse analyses from different engine inputs')
        db.execute('ATTACH DATABASE ? AS previous', (str(prior / 'corpus.sqlite3'),))
        db.execute('INSERT OR IGNORE INTO analyses SELECT p.surface,p.payload FROM previous.analyses p JOIN candidates c USING(surface)')
        db.commit()
        db.execute('DETACH DATABASE previous')
    defs = read_only(Path(baseline['normalize']) / 'definitions.sqlite3')
    analyze(db, defs, out)
    for limit in sorted(set(args.limits)):
        directory = mappings(db, defs, out, limit)
        print(json.dumps(read_json(directory / 'coverage.json'), ensure_ascii=True), flush=True)
        if args.export_limit == limit:
            export(latest=dict(normalize=baseline['normalize'], analyze=str(directory)),
                   destination=directory / 'yomitan', revision=f'corpus-2026-10-01-rc1-{limit}', corpus=True)
    defs.close()
    db.close()


if __name__ == '__main__':
    main()
