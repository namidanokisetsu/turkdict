"""Account for the entire observed union without an arbitrary analysis cutoff."""
import argparse
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from turkdict.common import digest, read_json, write_json
from scripts.build_yomitan import analyze, mappings, noise, read_only


def select(out,baseline):
    receipt=read_json(ROOT/'data/macocu/report.json')
    if not receipt.get('complete') or receipt.get('limited_documents'):
        raise ValueError('Complete MaCoCu import required')
    mac=ROOT/'data/macocu/frequency.sqlite3'
    freq=Path(baseline['frequency'])/'frequency.sqlite3'
    stamp=dict(macocu=digest(mac),extraction=digest(ROOT/'data/macocu/report.json'),
               frequency=digest(freq),limit='all-observed',policy='observed-rank-fusion-v1')
    inputs=out/'selection-inputs.json'
    if inputs.exists() and read_json(inputs)!=stamp:raise ValueError('Input snapshot changed')
    write_json(inputs,stamp)
    db=sqlite3.connect(out/'corpus.sqlite3',uri=True)
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='selection_complete'").fetchone():return db
    # A failed first selection rolls back as a unit. Raw corpora remain read-only.
    db.execute('ATTACH DATABASE ? AS mac',(mac.resolve().as_uri()+'?mode=ro',))
    db.execute('ATTACH DATABASE ? AS freq',(freq.resolve().as_uri()+'?mode=ro',))
    db.execute('BEGIN')
    try:
        db.execute('''CREATE TABLE candidates(surface TEXT PRIMARY KEY,subtitle_count INTEGER,subtitle_rank INTEGER,
            macocu_count INTEGER,macocu_rank INTEGER,wordfreq_rank INTEGER,score REAL,rank INTEGER)''')
        print('Indexing every MaCoCu spelling with separate subtitle/wordfreq signals',flush=True)
        db.execute('''INSERT INTO candidates
            SELECT m.surface,coalesce(s.value,0),s.rank,m.occurrences,m.source_rank,w.rank,
                coalesce(1.0/(60+s.rank),0)+1.0/(60+m.source_rank),NULL
            FROM (SELECT surface,occurrences,row_number() OVER (ORDER BY occurrences DESC,surface) source_rank FROM mac.counts) m
            LEFT JOIN freq.signals s ON s.surface=m.surface AND s.source='subtitles'
            LEFT JOIN freq.signals w ON w.surface=m.surface AND w.source='wordfreq' ''')
        print('Adding subtitle-only spellings',flush=True)
        db.execute('''INSERT INTO candidates
            SELECT s.surface,s.value,s.rank,0,NULL,w.rank,1.0/(60+s.rank),NULL FROM freq.signals s
            LEFT JOIN freq.signals w ON w.surface=s.surface AND w.source='wordfreq'
            WHERE s.source='subtitles' AND NOT EXISTS(SELECT 1 FROM candidates c WHERE c.surface=s.surface)''')
        totals=db.execute('''SELECT count(*),sum(macocu_count>0),sum(subtitle_count>0),
            sum(macocu_count>0 AND subtitle_count>0),sum(macocu_count),sum(subtitle_count) FROM candidates''').fetchone()
        # Both retained source adapters already enforce valid_token. Apply the
        # same explicit web-token exclusions as the measured sample builds.
        excluded=[]
        for token in ('www','http','https','html','htm','php','com','jpg','jpeg','png','nbsp'):
            if noise(token) and db.execute('SELECT 1 FROM candidates WHERE surface=?',(token,)).fetchone():
                excluded.append(token)
                db.execute('DELETE FROM candidates WHERE surface=?',(token,))
        print('Ranking the complete union on disk',flush=True)
        db.execute('CREATE TABLE ordering(surface TEXT PRIMARY KEY,rank INTEGER) WITHOUT ROWID')
        db.execute('INSERT INTO ordering SELECT surface,row_number() OVER(ORDER BY score DESC,surface) FROM candidates')
        db.execute('UPDATE candidates SET rank=(SELECT rank FROM ordering WHERE ordering.surface=candidates.surface)')
        db.execute('DROP TABLE ordering')
        db.execute('CREATE INDEX candidate_rank ON candidates(rank)')
        db.execute('CREATE TABLE analyses(surface TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        db.execute('CREATE TABLE redirects(surface TEXT,target TEXT,features TEXT,provenance TEXT,PRIMARY KEY(surface,target,features))')
        db.execute('CREATE TABLE selection_complete(ok INTEGER)')
        db.execute('INSERT INTO selection_complete VALUES(1)')
        db.commit()
        report=dict(zip(('union_forms','macocu_forms','subtitle_forms','overlap_forms','macocu_occurrences','subtitle_occurrences'),totals))
        report.update(selected_forms=totals[0]-len(excluded),excluded_web_tokens=excluded,
                      analysis_scope='complete observed union; no frequency or form-count cutoff',
                      weights={'macocu':1,'subtitles':1},wordfreq='reference-only',
                      note='Observed spellings are not all valid Turkish words. No-analysis and no-definition results are retained, not described as proven noise.')
        write_json(out/'inventory.json',report)
    except BaseException:
        db.rollback()
        raise
    db.execute('DETACH DATABASE mac')
    db.execute('DETACH DATABASE freq')
    return db


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=ROOT/'build/yomitan-full-inventory')
    p.add_argument('--reuse-analysis',type=Path,help='Optional earlier observed-corpus checkpoint directory')
    args=p.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    baseline=read_json(ROOT/'build/macocu-baseline-latest.json')['outputs']
    for stage,filename in [('normalize','definitions.sqlite3'),('frequency','frequency.sqlite3')]:
        folder=Path(baseline[stage])
        if digest(folder/filename)!=read_json(folder/'receipt.json')['outputs'][filename]:raise ValueError('Corrupt pinned baseline')
    db=select(out,baseline)
    prior=args.reuse_analysis or ROOT/'build/yomitan-corpus-2m'
    complete=db.execute('SELECT count(*) FROM analyses').fetchone()[0]==read_json(out/'inventory.json')['selected_forms']
    if not complete and (args.reuse_analysis or prior.exists()):
        prior=prior.resolve()
        old=read_json(prior/'selection-inputs.json');new=read_json(out/'selection-inputs.json')
        if any(old[k]!=new[k] for k in ('macocu','extraction','frequency','policy')):raise ValueError('Reuse corpus mismatch')
        if any(digest(ROOT/name)!=value for name,value in read_json(prior/'analysis-inputs.json').items()):raise ValueError('Reuse engine mismatch')
        db.execute('ATTACH DATABASE ? AS previous',((prior/'corpus.sqlite3').as_uri()+'?mode=ro',))
        db.execute('INSERT OR IGNORE INTO analyses SELECT p.surface,p.payload FROM previous.analyses p JOIN candidates c USING(surface)')
        db.commit();db.execute('DETACH DATABASE previous')
    defs=read_only(Path(baseline['normalize'])/'definitions.sqlite3')
    print('Analyzing the full inventory with checkpoint reuse',flush=True)
    analyze(db,defs,out)
    size=db.execute('SELECT count(*) FROM candidates').fetchone()[0]
    print('Linking all observed analyses to pinned definitions',flush=True)
    directory=mappings(db,defs,out,size)
    print(str(directory),flush=True)
    defs.close();db.close()


if __name__=='__main__':main()
