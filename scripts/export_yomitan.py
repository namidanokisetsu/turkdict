"""Stream complete definition dictionaries and observed-only Forms to Yomitan ZIPs."""
import argparse
from collections import Counter
from functools import lru_cache
from itertools import groupby
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from turkdict.common import read_json, write_json
from scripts.export_prototype import archive, compact_alternatives, render_definition, structured, Structured
from scripts.preview import morphology_rows, source_content


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('sample',type=Path)
    args=p.parse_args()
    sample=args.sample.resolve()
    if not (sample/'coverage.json').exists():
        raise ValueError('Completed mapping/coverage stage required')
    destination=sample/'yomitan-full'
    destination.mkdir(parents=True,exist_ok=True)
    baseline=read_json(ROOT/'build/macocu-baseline-latest.json')['outputs']
    db=sqlite3.connect((Path(baseline['normalize'])/'definitions.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    settings=read_json(ROOT/'config/build.json')['presentation']
    known=set()
    counts={}
    kinds={}
    revision='corpus-2026-10-01-rc1-full-definitions'
    for lang,name,title in [('tr','mono','Turkish Monolingual'),('en','bilingual','Turkish Bilingual')]:
        kinds[name]=Counter()
        def definitions():
            cursor=db.execute('SELECT key,id,senses,spelling FROM entries WHERE language=? ORDER BY key,id',(lang,))
            for lemma,group in groupby(cursor,key=lambda r:r[0]):
                records=[(eid.split(':')[0],json.loads(raw),spelling) for _,eid,raw,spelling in group]
                body=render_definition(lemma,lang,records,settings)
                if body:
                    known.add(lemma)
                    kinds[name]['phrases' if any(c.isspace() for c in lemma) else 'single_tokens']+=1
                    yield [lemma,'','','',0,[structured(body)],-1,'']
        counts[name]=archive(destination/f'turkish-{name}.zip',title,definitions(),revision)
        print(json.dumps({name:counts[name], 'kinds':kinds[name]}),flush=True)
    def etymologies():
        cursor=db.execute("SELECT key,senses FROM entries WHERE id LIKE 'wty:%' ORDER BY key,id")
        for lemma,group in groupby(cursor,key=lambda r:r[0]):
            senses=[s for _,raw in group for s in json.loads(raw)]
            fragment=source_content('wty',senses,lemma,dict(settings,etymology_only=True))
            if fragment:yield [lemma,'','','',0,[structured(fragment)],-1,'']
    counts['etymology']=archive(destination/'turkish-etymology.zip','Turkish Etymology',etymologies(),revision)

    @lru_cache(maxsize=None)
    def target(eid):
        row=db.execute('SELECT key,language FROM entries WHERE id=?',(eid,)).fetchone()
        if row is None:raise ValueError('Missing canonical target')
        return row

    @lru_cache(maxsize=100000)
    def labels(features):
        fragment=morphology_rows([dict(features=json.loads(features),provenance={'raw':'analysis'})])
        parser=Structured()
        parser.feed(fragment)
        def texts(nodes):
            for node in nodes:
                if isinstance(node,str):
                    if node.strip():yield node.strip()
                else:yield from texts(node.get('content',[]))
        return list(texts(parser.content)) or ['inflected form']

    def forms():
        seen=set()
        with (sample/'mappings.jsonl').open(encoding='utf-8') as stream:
            records=(json.loads(line) for line in stream)
            for surface,group in groupby(records,key=lambda r:r['surface']):
                if surface in seen:raise ValueError('Canonical mappings must group each surface contiguously')
                seen.add(surface)
                alternatives=[]
                for record in group:
                    if record['origin']!='observed':raise ValueError('Synthetic form rejected')
                    lemma,lang=target(record['target_lemma_id'])
                    if lang!=record['definition_language']:raise ValueError('Wrong-direction target')
                    if surface==lemma:continue
                    if lemma not in known:raise ValueError('No rendered definition for '+lemma)
                    alternative=[lemma,labels(json.dumps(record['features'],ensure_ascii=False,sort_keys=True))]
                    if alternative not in alternatives:alternatives.append(alternative)
                if alternatives:yield [surface,'','','',0,compact_alternatives(alternatives),-1,'']
    # Alphabetical bank order substantially improves ZIP compression without
    # changing lookup content. Sort on disk rather than retaining millions of
    # parsed records in Python memory.
    form_db=sqlite3.connect(destination/'forms.sqlite3')
    form_db.execute('CREATE TABLE IF NOT EXISTS forms(surface TEXT PRIMARY KEY,payload TEXT NOT NULL)')
    form_db.execute('DELETE FROM forms')
    form_links=0
    for i,row in enumerate(forms(),1):
        form_links+=len(row[5])
        form_db.execute('INSERT INTO forms VALUES(?,?)',(row[0],json.dumps(row,ensure_ascii=False)))
        if i%10000==0:form_db.commit()
    form_db.commit()
    counts['forms']=archive(destination/'turkish-forms.zip','Turkish Forms',
        (json.loads(raw) for raw, in form_db.execute('SELECT payload FROM forms ORDER BY surface')),revision)
    form_db.close()
    report=dict(entries=counts,definition_kinds=kinds,revision=revision,macocu_included=True,
        definition_scope='All usable normalized headwords and phrases, independently of the Forms edition',
        corpus_coverage_note='Selection coverage measures ranked analyzed forms only; shipped definitions also match unselected exact headwords.',
        source_build=baseline,form_lemma_links=form_links,coverage=read_json(sample/'coverage.json'))
    write_json(destination/'report.json',report)
    print(json.dumps(counts),flush=True)
    db.close()


if __name__=='__main__':main()
