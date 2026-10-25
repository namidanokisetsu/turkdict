"""Evaluate the draft learner cases against cached observed-corpus analyses."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from turkdict.common import key, read_json, write_json


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('corpus',type=Path)
    args=p.parse_args()
    db=sqlite3.connect(args.corpus/'corpus.sqlite3')
    cases=read_json(ROOT/'evaluation/cases.json')
    results=[]
    for case in cases['cases']:
        word=key(case['surface'])
        raw=db.execute('SELECT payload FROM analyses WHERE surface=?',(word,)).fetchone()
        actual=json.loads(raw[0]) if raw else []
        expectations=[]
        for expected in case['expected']:
            same=[a for a in actual if a['lemma']==key(expected['lemma'])]
            def matches(a):
                features={f['id'] for f in a['features']}
                # An inability morpheme is semantically negative; do not call
                # different engine tag conventions a proven linguistic error.
                if 'Unable' in features: features.add('Neg')
                return set(expected['features'])<=features
            expectations.append(dict(expected=expected,lemma_recovered=bool(same),features_recovered=any(matches(a) for a in same)))
        redirects=[dict(target=t,labels=json.loads(f)) for t,f in db.execute('SELECT target,features FROM redirects WHERE surface=?',(word,))]
        results.append(dict(surface=case['surface'],category=case['category'],selected=raw is not None,
                            negative=case.get('negative',False),expectations=expectations,
                            engine_analyses=actual,wiktionary_evidence=redirects))
    expected=[e for row in results for e in row['expectations']]
    report=dict(review_status=cases['review_status'],draft_cases=len(results),expected_analyses=len(expected),
                lemma_recovered=sum(e['lemma_recovered'] for e in expected),
                feature_constraints_recovered=sum(e['features_recovered'] for e in expected),
                unselected_cases=[r['surface'] for r in results if not r['selected']],
                semantics='Unable also satisfies draft Neg constraint. No exhaustive precision or human validation claimed.',
                cases=results)
    write_json(args.corpus/'draft-case-review.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='cases'},ensure_ascii=True))
    db.close()


if __name__=='__main__':main()
