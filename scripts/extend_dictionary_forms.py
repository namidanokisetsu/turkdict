"""Merge every usable pinned dictionary redirect into the complete corpus export."""
import argparse
from collections import Counter
from itertools import groupby
import json
from pathlib import Path
import shutil
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.build_yomitan import source_redirect_labels
from scripts.export_prototype import archive, compact_alternatives
from scripts.package_yomitan import banks
from turkdict.common import digest, read_json, write_json


def usable_groups(defs, known, counts):
    """Yield sorted, direct source links; retain labels without a corpus gate."""
    rows=defs.execute('SELECT surface,target,features,provenance FROM redirects ORDER BY surface,target,features')
    for surface,group in groupby(rows,key=lambda row:row[0]):
        alternatives=[]
        evidence=[]
        for _,target,features,provenance in group:
            counts['source_records']+=1
            if surface==target:
                counts['self_links']+=1
                continue
            labels=source_redirect_labels(json.loads(features)['raw'])
            if labels is None:
                counts['unsupported_labels']+=1
                continue
            if target not in known:
                counts['missing_direct_definition']+=1
                continue
            alternatives.append([target,labels])
            evidence.append((target,features,provenance))
        if alternatives:
            counts['usable_spellings']+=1
            counts['usable_distinct_links']+=len({a[0] for a in alternatives})
            yield surface,alternatives,evidence


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=ROOT/'build/yomitan-full-inventory/sample-17386877/yomitan-full')
    p.add_argument('--destination',type=Path,default=ROOT/'build/yomitan-dictionary-plus-corpus')
    args=p.parse_args()
    source=args.source.resolve(); destination=args.destination.resolve()
    if destination.exists():raise ValueError('Use a fresh destination; retained exports are not overwritten')
    destination.mkdir(parents=True)
    report=read_json(source/'report.json')
    known=set()
    for name in ('mono','bilingual','etymology'):
        filename=f'turkish-{name}.zip'
        shutil.copy2(source/filename,destination/filename)
        if name!='etymology':known.update(row[0] for row in banks(source/filename))
    shutil.copy2(source/'forms.sqlite3',destination/'forms.sqlite3')
    forms=sqlite3.connect(destination/'forms.sqlite3')
    forms.execute('CREATE TABLE dictionary_evidence(surface TEXT,target TEXT,features TEXT,provenance TEXT,PRIMARY KEY(surface,target,features)) WITHOUT ROWID')
    baseline=read_json(ROOT/'build/macocu-baseline-latest.json')['outputs']
    path=Path(baseline['normalize'])/'definitions.sqlite3'
    defs=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
    counts=Counter()
    for i,(surface,alternatives,evidence) in enumerate(usable_groups(defs,known,counts),1):
        found=forms.execute('SELECT payload FROM forms WHERE surface=?',(surface,)).fetchone()
        row=json.loads(found[0]) if found else [surface,'','','',0,[],-1,'']
        previous={lemma for lemma,_ in row[5]}
        counts['added_spellings']+=found is None
        counts['added_distinct_links']+=len({lemma for lemma,_ in alternatives}-previous)
        # Existing pairs already carry the corpus export's compact grammar.
        # Add new targets without repeating source paraphrases of those pairs.
        # Full source labels/provenance remain in dictionary_evidence.
        row[5]=compact_alternatives(row[5]+[a for a in alternatives if a[0] not in previous])
        forms.execute('INSERT OR REPLACE INTO forms VALUES(?,?)',(surface,json.dumps(row,ensure_ascii=False)))
        forms.executemany('INSERT OR IGNORE INTO dictionary_evidence VALUES(?,?,?,?)',
                          ((surface,*record) for record in evidence))
        if i%10000==0:forms.commit()
        if i%100000==0:print(f'Merged {i:,} dictionary spellings; {counts["added_spellings"]:,} new',flush=True)
    forms.commit();defs.close()
    revision='2026-10-01-dictionary-plus-corpus'
    total=archive(destination/'turkish-forms.zip','Turkish Forms',
        (json.loads(raw) for raw, in forms.execute('SELECT payload FROM forms ORDER BY surface')),revision)
    forms.close()
    report['entries']['forms']=total
    report['form_lemma_links']+=counts['added_distinct_links']
    report.update(revision=revision,forms_policy='dictionary-attested plus corpus-observed',
        dictionary_expansion=dict(counts=counts,base_export=str(source),base_forms_sha256=digest(source/'turkish-forms.zip'),
                                  definitions_sha256=digest(path),locally_generated_forms=0),
        corpus_coverage_note='Corpus coverage describes the observed baseline; dictionary-only forms are additional and do not imply corpus occurrence.')
    write_json(destination/'report.json',report)
    print(json.dumps(dict(entries=report['entries'],links=report['form_lemma_links'],expansion=counts)),flush=True)


if __name__=='__main__':main()
