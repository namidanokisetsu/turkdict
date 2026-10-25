import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts.build_yomitan import grammar_labels, mappings, noise, variant_labels, source_redirect_labels
from scripts.export_prototype import archive
from scripts import build_full_inventory
from scripts.extend_dictionary_forms import usable_groups
from collections import Counter
from turkdict.common import digest, write_json


class ReleaseTests(unittest.TestCase):
    def test_dictionary_forms_do_not_require_corpus_occurrence_but_need_definitions(self):
        db=sqlite3.connect(':memory:')
        db.execute('CREATE TABLE redirects(surface TEXT,target TEXT,features TEXT,provenance TEXT)')
        rows=[('unobserved','ev',['plural']),('unobserved','ev',['dative']),
              ('missing','nowhere',['plural']),('ev','ev',['form']),('case','ev',['uppercase']),
              ('variant','ev',['dialectal Isparta'])]
        db.executemany('INSERT INTO redirects VALUES(?,?,?,?)',
            [(word,target,json.dumps({'raw':[target,labels]}),'{}') for word,target,labels in rows])
        counts=Counter()
        groups=list(usable_groups(db,{'ev'},counts))
        self.assertEqual([row[0] for row in groups],['unobserved','variant'])
        self.assertEqual(groups[0][1],[['ev',['dative']],['ev',['plural']]])
        self.assertEqual(counts['usable_distinct_links'],2)
        self.assertEqual(counts['missing_direct_definition'],1)
        self.assertEqual(counts['self_links'],1)
        self.assertEqual(counts['unsupported_labels'],1)
        db.close()

    def test_full_inventory_has_no_rank_cutoff_or_wordfreq_only_additions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'data/macocu').mkdir(parents=True)
            (root/'frequency').mkdir()
            (root/'output').mkdir()
            write_json(root/'data/macocu/report.json',dict(complete=True,limited_documents=0))
            mac=sqlite3.connect(root/'data/macocu/frequency.sqlite3')
            mac.execute('CREATE TABLE counts(surface TEXT PRIMARY KEY,occurrences INTEGER)')
            mac.executemany('INSERT INTO counts VALUES(?,?)',[('ev',200),('kitap',80),('www',10)])
            mac.commit();mac.close()
            freq=sqlite3.connect(root/'frequency/frequency.sqlite3')
            freq.execute('CREATE TABLE signals(surface TEXT,source TEXT,value REAL,rank INTEGER,PRIMARY KEY(surface,source))')
            freq.executemany('INSERT INTO signals VALUES(?,?,?,?)',[('ev','subtitles',100,1),('yazar','subtitles',50,2),('unobserved','wordfreq',1,1)])
            freq.commit();freq.close()
            with patch.object(build_full_inventory,'ROOT',root):
                db=build_full_inventory.select(root/'output',dict(frequency=str(root/'frequency')))
            rows=list(db.execute('SELECT surface,subtitle_count,macocu_count FROM candidates ORDER BY rank'))
            self.assertEqual(rows,[('ev',100,200),('kitap',0,80),('yazar',50,0)])
            inventory=json.loads((root/'output/inventory.json').read_text())
            self.assertEqual(inventory['union_forms'],4)
            self.assertEqual(inventory['overlap_forms'],1)
            self.assertEqual(inventory['macocu_occurrences'],290)
            self.assertEqual(inventory['subtitle_occurrences'],150)
            db.close()

    def test_only_explicit_grammar_can_recover_observed_forms(self):
        self.assertEqual(grammar_labels(['kitap', ['accusative singular definite']]), ['accusative singular definite'])
        self.assertEqual(grammar_labels(['demek', ['converb']]), ['converb'])
        self.assertEqual(grammar_labels(['demek', ['perfective temporal adverb']]), ['perfective temporal adverb'])
        for labels in (['misspelling'], ['alternative form'], ['uppercase'], ['plural', 'misspelling'], []):
            self.assertIsNone(grammar_labels(['target', labels]))
        self.assertEqual(noise('www'), 'web-token')
        self.assertIsNone(noise("ankara'dan"))
        self.assertEqual(variant_labels(['orijinal',['alternative proscribed']]),['alternative proscribed'])
        self.assertIsNone(variant_labels(['target',['uppercase']]))
        self.assertEqual(source_redirect_labels(['target',['dialectal Isparta']]),['dialectal Isparta'])
        self.assertIsNone(source_redirect_labels(['target',['uppercase']]))
        self.assertIsNone(source_redirect_labels(['target',[{'invalid':'label'}]]))
        self.assertEqual(grammar_labels(['ev',['dative first-person singular possessed-single possessive']]),
                         ['dative first-person singular possessed-single possessive'])

    def test_observed_only_direct_targets_and_uninflated_pair_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            db = sqlite3.connect(':memory:')
            db.executescript('''CREATE TABLE candidates(surface TEXT,subtitle_count INTEGER,macocu_count INTEGER,rank INTEGER);
                CREATE TABLE analyses(surface TEXT,payload TEXT);
                CREATE TABLE redirects(surface TEXT,target TEXT,features TEXT,provenance TEXT);''')
            defs = sqlite3.connect(':memory:')
            defs.execute('CREATE TABLE entries(id TEXT,key TEXT,language TEXT)')
            defs.executemany('INSERT INTO entries VALUES(?,?,?)', [('tdk:1','ev','tr'), ('tdk:2','ev','tr'), ('tureng:1','ev','en')])
            db.executemany('INSERT INTO candidates VALUES(?,?,?,?)', [('evler',20,100,1), ('missing',2,5,2)])
            db.executemany('INSERT INTO analyses VALUES(?,?)', [('evler','[]'), ('missing','[]')])
            db.executemany('INSERT INTO redirects VALUES(?,?,?,?)', [('evler','ev','["plural"]','{}'),
                ('missing','nowhere','["plural"]','{}'), ('unobserved','ev','["plural"]','{}')])
            write_json(out / 'inventory.json', dict(subtitle_occurrences=22, macocu_occurrences=105))
            result = mappings(db, defs, out, 2)
            report = json.loads((result / 'coverage.json').read_text())
            self.assertEqual(report['counts']['surface_lemma_pairs'], 1)
            self.assertEqual(report['counts']['grammatical_analyses'], 1)
            self.assertEqual(report['counts']['linked_forms'], 1)
            self.assertEqual(report['counts']['linked_tr'], 1)
            self.assertEqual(report['counts']['linked_en'], 1)
            records = [json.loads(s) for s in (result / 'mappings.jsonl').read_text().splitlines()]
            self.assertEqual(len(records), 2)
            self.assertEqual({r['surface'] for r in records}, {'evler'})
            self.assertTrue(all(r['origin'] == 'observed' for r in records))
            db.close()
            defs.close()

    def test_yomitan_archive_is_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / 'a.zip', Path(tmp) / 'b.zip'
            rows = [['ev','','','',0,['house'],-1,'']]
            archive(a, 'Test', rows, 'release-test')
            archive(b, 'Test', rows, 'release-test')
            self.assertEqual(digest(a), digest(b))


if __name__ == '__main__':
    unittest.main()
