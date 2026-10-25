import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import sqlite3
import tempfile
import zipfile
import zlib

from . import definitions, frequency, morphology
from .common import digest, dumps, identity, key, read_json, write_json
from .sources import fetch, verify, verify_environment


class Pipeline:
    def __init__(self, root, config):
        self.root = Path(root).resolve()
        self.config = read_json(config)
        self.sources = read_json(self.root / self.config['manifest'])
        self.sources = [s for s in self.sources if s['id'] not in self.config.get('disabled_sources', [])]
        verify_environment(self.root)
        self.cache = self.root / self.config.get('cache', 'cache/stages')
        self.cache.mkdir(parents=True, exist_ok=True)
        self.outputs = {}
        self.hits = []

    def cached(self, stage, inputs, run):
        code = {p.name: digest(p) for p in sorted(Path(__file__).parent.glob('*.py'))}
        stamp = identity(dict(stage=stage, inputs=inputs, code=code,
                              python=platform.python_version(), sqlite=sqlite3.sqlite_version,
                              zlib=zlib.ZLIB_RUNTIME_VERSION,
                              resources=digest(self.root / 'config/resources.lock.json') if (self.root / 'config/resources.lock.json').exists() else None,
                              dependencies=digest(self.root / 'requirements.lock') if (self.root / 'requirements.lock').exists() else None))
        target = self.cache / stage / stamp
        if (target / 'receipt.json').exists():
            receipt = read_json(target / 'receipt.json')
            if all((target / name).is_file() and digest(target / name) == h for name, h in receipt['outputs'].items()):
                self.hits.append(stage)
                self.outputs[stage] = target
                return target
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=target.parent) as temp:
            temp = Path(temp)
            report = run(temp)
            write_json(temp / 'report.json', report)
            write_json(temp / 'receipt.json', dict(inputs=inputs, outputs={p.name: digest(p) for p in sorted(temp.iterdir())}))
            if target.exists():
                # Only this content-addressed cache entry may be replaced.
                if not target.resolve().is_relative_to(self.cache.resolve()):
                    raise ValueError('Unsafe cache path')
                shutil.rmtree(target)
            shutil.copytree(temp, target)
        self.outputs[stage] = target
        return target

    def run(self, stage):
        if stage in self.outputs:
            return self.outputs[stage]
        if stage == 'fetch':
            for source in self.sources:
                fetch(source, self.root)
            return None
        if stage in ('normalize', 'frequency'):
            subset = [s for s in self.sources if (s['direction'] == 'frequency') == (stage == 'frequency')]
            for s in subset:
                verify(self.root / s['path'], s)
            if stage == 'normalize':
                return self.cached(stage, subset, lambda out: definitions.normalize(subset, self.root, out))
            cfg = self.config['frequency']
            books = {p: digest(self.root / p) for p in cfg.get('books', [])}
            return self.cached(stage, dict(sources=subset, config=cfg, books=books),
                               lambda out: frequency.build(subset, self.root, out, cfg))
        if stage == 'analyze':
            defs = self.run('normalize')
            freq = self.run('frequency')
            cfg = self.config['morphology']
            engine_files = {p: digest(self.root / p) for p in ('config/java.lock.json', 'scripts/MorphologyBridge.java') if (self.root / p).exists()}
            return self.cached(stage, dict(definitions=defs.name, frequency=freq.name, config=cfg,
                                           weights=self.config['weights'], engine_files=engine_files),
                               lambda out: self.analyze(defs, freq, out))
        if stage == 'rank':
            analyzed = self.run('analyze')
            freq = self.run('frequency')
            return self.cached(stage, dict(analyze=analyzed.name, frequency=freq.name, weights=self.config['weights']),
                               lambda out: self.rank(analyzed, freq, out))
        if stage == 'export':
            ranked = self.run('rank')
            defs = self.run('normalize')
            def export(out):
                # Canonical build artifact only; platform dictionaries belong to stage 5.
                with zipfile.ZipFile(out / 'canonical.zip', 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                    for name, path in [('definitions.sqlite3', defs / 'definitions.sqlite3'), ('mappings.jsonl', ranked / 'mappings.jsonl')]:
                        info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                        info.create_system = 3
                        info.external_attr = 0o100644 << 16
                        info.compress_type = zipfile.ZIP_DEFLATED
                        with z.open(info, 'w', force_zip64=True) as dest, path.open('rb') as src:
                            shutil.copyfileobj(src, dest)
                return dict(format='canonical-v1', sha256=digest(out / 'canonical.zip'))
            return self.cached(stage, dict(rank=ranked.name, definitions=defs.name), export)
        if stage in ('validate', 'build'):
            exported = self.run('export')
            def validate(out):
                db = sqlite3.connect(self.run('normalize') / 'definitions.sqlite3')
                count = 0
                try:
                    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise ValueError('Invalid normalized database')
                    with (self.run('rank') / 'mappings.jsonl').open(encoding='utf-8') as mappings:
                        for line in mappings:
                            m = json.loads(line)
                            target = db.execute('SELECT senses,language FROM entries WHERE id=?', (m['target_lemma_id'],)).fetchone()
                            if not target or not json.loads(target[0]) or target[1] != m['definition_language']:
                                raise ValueError('Dangling or wrong-direction mapping')
                            if not m['features'] or not m['provenance']:
                                raise ValueError('Missing mapping evidence')
                            count += 1
                    with zipfile.ZipFile(exported / 'canonical.zip') as z:
                        if z.testzip() is not None:
                            raise ValueError('Corrupt canonical archive')
                finally:
                    db.close()
                return dict(valid=True, mappings=count, platform_validation=False)
            return self.cached('validate', dict(export=exported.name), validate)
        raise ValueError(stage)

    def analyze(self, defs, freq, out):
        db = sqlite3.connect(defs / 'definitions.sqlite3')
        fdb = sqlite3.connect(freq / 'frequency.sqlite3')
        cfg = self.config['morphology']
        engine = morphology.create(cfg['engine'], self.root) if cfg['engine'] != 'fixture' else None
        records = {}
        report = dict(observed_forms=0, unanalyzed=0, no_definition=0, generated_candidates=0, generated_roundtrip_rejected=0)
        def add(surface, analysis, origin):
            targets = db.execute('SELECT id,language FROM entries WHERE key=? ORDER BY id', (key(analysis['lemma']),)).fetchall()
            if not targets:
                report['no_definition'] += 1
            for target, language in targets:
                record = dict(surface=key(surface), display_surface=surface, target_lemma_id=target,
                              definition_language=language, features=analysis['features'], segmentation=analysis.get('segmentation'),
                              origin=origin, evidence=('dictionary-attested' if analysis['raw'] == 'exact' else
                                                       'engine-analysis' if origin == 'observed' else 'engine-roundtrip'),
                              provenance=dict(engine=engine.identity if engine else 'fixture', raw=analysis['raw'],
                                              analysis_lemma=analysis['lemma']))
                records[dumps(record)] = record
        try:
            candidates = list(frequency.ranked(fdb, self.config['weights']).fetchmany(cfg['observed_limit']))
            for surface, score in candidates:
                report['observed_forms'] += 1
                # Exact lexical matches survive alongside any root analyses.
                if db.execute('SELECT 1 FROM entries WHERE key=? LIMIT 1', (surface,)).fetchone():
                    add(surface, dict(lemma=surface, features=[dict(id='Lexical', label='dictionary headword')], raw='exact'), 'observed')
                analyses = engine.analyze(surface) if engine else cfg.get('fixture_analyses', {}).get(surface, [])
                report['unanalyzed'] += not analyses
                for a in analyses:
                    add(surface, a, 'observed')
            for lemma in cfg.get('generate_lemmas', []):
                if not isinstance(engine, morphology.Zemberek):
                    raise ValueError('Bounded generation currently requires Zemberek')
                for tags in cfg.get('generation_templates', []):
                    for a in engine.generate(lemma, tags):
                        report['generated_candidates'] += 1
                        roundtrip = engine.analyze(a['surface'])
                        if any(p['lemma'] == a['lemma'] and p['features'] == a['features'] for p in roundtrip):
                            add(a['surface'], a, 'generated')
                        else:
                            report['generated_roundtrip_rejected'] += 1
            with (out / 'mappings.jsonl').open('w', encoding='utf-8', newline='\n') as stream:
                for k in sorted(records):
                    stream.write(k + '\n')
            report['mapping_analyses'] = len(records)
            report['unique_surface_lemma_pairs'] = len({(m['surface'], m['target_lemma_id']) for m in records.values()})
            report['unique_surfaces'] = len({m['surface'] for m in records.values()})
            report['selection_status'] = cfg.get('selection_status', 'fixture')
            return report
        finally:
            db.close()
            fdb.close()
            if engine:
                engine.close()

    def rank(self, analyzed, freq, out):
        db = sqlite3.connect(freq / 'frequency.sqlite3')
        try:
            scores = dict(frequency.ranked(db, self.config['weights']))
        finally:
            db.close()
        with (analyzed / 'mappings.jsonl').open(encoding='utf-8') as f:
            rows = [json.loads(line) for line in f]
        rows.sort(key=lambda m: (-scores.get(m['surface'], 0), m['surface'], m['target_lemma_id'], dumps(m)))
        with (out / 'mappings.jsonl').open('w', encoding='utf-8', newline='\n') as f:
            for row in rows:
                row['score'] = scores.get(row['surface'], 0)
                f.write(dumps(row) + '\n')
        return dict(analyses=len(rows), ranking='weighted reciprocal rank, k=60; sources may overlap')
