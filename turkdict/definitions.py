"""Lossless source-specific senses in a shared, indexed lemma representation."""
from collections import defaultdict
import json
import re
import sqlite3
import unicodedata
import zipfile
import xml.etree.ElementTree as ET

from .common import dumps, identity, key

POS = {"isim": "NOUN", "sıfat": "ADJ", "zarf": "ADV", "zamir": "PRON",
       "edat": "ADP", "bağlaç": "CCONJ", "ünlem": "INTJ"}


def schema(db):
    db.executescript('''
        CREATE TABLE entries(id TEXT PRIMARY KEY, spelling TEXT NOT NULL, key TEXT NOT NULL,
            pos TEXT, kind TEXT NOT NULL, language TEXT NOT NULL, senses TEXT NOT NULL,
            provenance TEXT NOT NULL);
        CREATE INDEX entry_key ON entries(key);
        CREATE TABLE redirects(surface TEXT, target TEXT, features TEXT, provenance TEXT,
            PRIMARY KEY(surface,target,features,provenance));
        CREATE INDEX redirect_target ON redirects(target);
        CREATE TABLE semantic_synsets(id TEXT PRIMARY KEY,source TEXT NOT NULL,data TEXT NOT NULL);
    ''')


def provenance(source, record):
    return dict(source=source['id'], snapshot=source['snapshot'], sha256=source['sha256'],
                parser=source['parser'], record=str(record))


def insert(db, source, sid, spelling, pos, senses, **extra):
    spelling = unicodedata.normalize('NFC', spelling.strip())
    if not spelling or not senses:
        return False
    # Source record identity keeps separate homographs; equivalent senses deduplicate within it.
    unique = list(dict.fromkeys(dumps(s) for s in senses))
    kind = 'phrase' if any(c.isspace() for c in spelling) else 'lexical'
    prov = dict(provenance(source, sid), **extra)
    db.execute('INSERT INTO entries VALUES(?,?,?,?,?,?,?,?)',
               (f"{source['id']}:{sid}", spelling, key(spelling), pos, kind,
                source['language'], '[' + ','.join(unique) + ']', dumps(prov)))
    return True


def tdk(db, path, source):
    src = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    src.row_factory = sqlite3.Row
    labels, examples, senses = defaultdict(list), defaultdict(list), defaultdict(list)
    report = dict(records=0, rejected=0, empty_senses=0)
    try:
        if src.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Corrupt TDK database')
        report['source_foreign_key_errors'] = len(src.execute('PRAGMA foreign_key_check').fetchall())
        for r in src.execute('SELECT a.anlam_id,o.tam_adi FROM anlam_ozellik a JOIN ozellik o USING(ozellik_id) ORDER BY a.anlam_id,o.ozellik_id'):
            labels[r['anlam_id']].append(r['tam_adi'])
        for r in src.execute('SELECT o.*,y.tam_adi AS author FROM ornek o LEFT JOIN yazar y USING(yazar_id) ORDER BY anlam_id,ornek_sira,ornek_id'):
            if r['ornek']:
                examples[r['anlam_id']].append(dict(text=r['ornek'], author=r['author'] or r['yazar_vd']))
        for r in src.execute('SELECT * FROM anlam ORDER BY madde_id,anlam_sira,anlam_id'):
            if not r['anlam'].strip():
                report['empty_senses'] += 1
                continue
            tags = labels[r['anlam_id']]
            pos = 'VERB' if r['fiil'] or any(t in tags for t in ('nesnesiz', 'yardımcı  fiil', '-i', '-e', '-den', '-le', '-de')) else next((POS[t] for t in tags if t in POS), None)
            senses[r['madde_id']].append(dict(text=r['anlam'], pos=pos, labels=tags,
                                             examples=examples[r['anlam_id']], source_id=r['anlam_id']))
        for r in src.execute('SELECT * FROM madde ORDER BY madde_id'):
            report['records'] += 1
            ss = senses[r['madde_id']]
            ok = insert(db, source, r['madde_id'], r['madde'], None, ss,
                        pronunciation=r['telaffuz'], origin=r['lisan'], compounds=r['birlesikler'])
            report['rejected'] += not ok
    finally:
        src.close()
    return report


def tureng(db, path, source):
    with zipfile.ZipFile(path) as archive:
        data = json.loads(archive.read('tureng_database.json'))['tur']
    report = dict(records=len(data), rejected=0)
    for spelling, values in sorted(data.items()):
        senses = [dict(text=v) for v in values if isinstance(v, str) and v.strip()]
        report['rejected'] += not insert(db, source, identity(spelling), spelling, None, senses)
    return report


def wty(db, path, source):
    report = dict(records=0, rejected=0, definition_rows=0, redirect_rows=0)
    with zipfile.ZipFile(path) as archive:
        index = json.loads(archive.read('index.json'))
        if (index.get('format'), index.get('sourceLanguage'), index.get('targetLanguage')) != (3, 'tr', source['language']):
            raise ValueError('Unexpected Wiktionary dictionary format/direction')
        if index['revision'] != source['snapshot']:
            raise ValueError('Unexpected Wiktionary revision')
        banks = sorted((n for n in archive.namelist() if re.fullmatch(r'term_bank_\d+\.json', n)),
                       key=lambda n: int(n[10:-5]))
        for name in banks:
            for i, row in enumerate(json.loads(archive.read(name))):
                if len(row) != 8 or not isinstance(row[5], list):
                    raise ValueError(f'Invalid term {name}:{i}')
                report['records'] += 1
                definitions = []
                redirected = False
                for content in row[5]:
                    if isinstance(content, list):
                        if not content or not isinstance(content[0], str):
                            report['rejected'] += 1
                            continue
                        db.execute('INSERT OR IGNORE INTO redirects VALUES(?,?,?,?)',
                                   (key(row[0]), key(content[0]), dumps(dict(tags=row[2], term_tags=row[7], raw=content)),
                                    dumps(provenance(source, f'{name}:{i}'))))
                        redirected = True
                    else:
                        definitions.append(dict(content=content, tags=row[2], term_tags=row[7]))
                if definitions:
                    insert(db, source, f'{name}:{i}', row[0], row[2] or None, definitions)
                    report['definition_rows'] += 1
                report['redirect_rows'] += redirected
    return report


def kenet(db, path, source):
    report = dict(records=0, rejected=0, synsets=0, relations=0, missing_relation_targets=0)
    edges = []
    known = set()
    for _, node in ET.iterparse(path, events=('end',)):
        if node.tag != 'SYNSET':
            continue
        sid = node.findtext('ID')
        if not sid or sid in known:
            raise ValueError('Missing or duplicate KeNet synset ID')
        known.add(sid)
        members = [dict(spelling=(m.text or '').strip(), sense=m.findtext('SENSE', ''),
                        group=m.findtext('GROUP', ''),
                        lexical_relations=[dict(target=(r.text or '').strip(), kind=r.findtext('TYPE', ''),
                                                target_sense=r.findtext('TO', '')) for r in m.findall('SR')])
                   for m in node.findall('SYNONYM/LITERAL') if (m.text or '').strip()]
        relations = [dict(target_synset=(r.text or '').strip(), kind=r.findtext('TYPE', ''),
                          target_sense=r.findtext('TO', '')) for r in node.findall('SR')]
        edges.extend(r['target_synset'] for r in relations)
        pos = {'n': 'NOUN', 'v': 'VERB', 'a': 'ADJ', 'r': 'ADV'}.get(node.findtext('POS'))
        definitions = [d.text for d in node.findall('DEF') if d.text and d.text.strip()]
        examples = [e.text for e in node.findall('EXAMPLE') if e.text]
        semantic = dict(synset_id=sid, members=members, relations=relations,
                        definitions=definitions, examples=examples, pos=pos,
                        provenance=provenance(source, sid), raw_xml=ET.tostring(node, encoding='unicode'))
        db.execute('INSERT INTO semantic_synsets VALUES(?,?,?)', (sid, source['id'], dumps(semantic)))
        for i, member in enumerate(members):
            report['records'] += 1
            # Source synset/sense identity is never equated with a TDK sense number.
            senses = [dict(text=text, pos=pos, synset_id=sid, member_sense=member['sense'],
                           synonyms=members, relations=relations, examples=examples,
                           relation_scope='synset') for text in definitions]
            report['rejected'] += not insert(db, source, f'{sid}:{i}', member['spelling'], pos, senses)
        report['synsets'] += 1
        report['relations'] += len(relations)
        node.clear()
    report['missing_relation_targets'] = sum(target not in known for target in edges)
    return report


def normalize(sources, root, output):
    db = sqlite3.connect(output / 'definitions.sqlite3')
    report = {}
    try:
        schema(db)
        for source in sources:
            report[source['id']] = {'tdk-sqlite-v1': tdk, 'tureng-v1': tureng, 'wty-v1': wty, 'kenet-v1': kenet}[source['parser']](db, root / source['path'], source)
        for source in sources:
            prefix = source['id'] + ':%'
            report[source['id']]['usable_records'] = db.execute('SELECT count(*) FROM entries WHERE id LIKE ?', (prefix,)).fetchone()[0]
            report[source['id']]['usable_keys'] = db.execute('SELECT count(DISTINCT key) FROM entries WHERE id LIKE ?', (prefix,)).fetchone()[0]
            report[source['id']]['examples'] = [dict(id=r[0], spelling=r[1], senses=json.loads(r[2])) for r in db.execute('SELECT id,spelling,senses FROM entries WHERE id LIKE ? ORDER BY id LIMIT 3', (prefix,))]
        report['overlap_tr_en_keys'] = db.execute("SELECT count(*) FROM (SELECT key FROM entries GROUP BY key HAVING count(DISTINCT language)=2)").fetchone()[0]
        report['unresolved_redirects'] = db.execute('SELECT count(*) FROM redirects r WHERE NOT EXISTS(SELECT 1 FROM entries e WHERE e.key=r.target)').fetchone()[0]
        db.commit()
    finally:
        db.close()
    return report
