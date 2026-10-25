import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile

from turkdict.common import digest, key, read_json, valid_token, write_json
from turkdict.frequency import epub, ranked
from turkdict.pipeline import Pipeline
from turkdict.sources import verify


def fixtures(root):
    sources = []
    def source(id, parser, path, language, direction, snapshot='fixture'):
        sources.append(dict(id=id, parser=parser, path=path, sha256=digest(root / path),
                            url='https://invalid.example/never-fetch', language=language, direction=direction, snapshot=snapshot))
    db = sqlite3.connect(root / 'tdk.db')
    db.executescript('''
        CREATE TABLE madde(madde_id INTEGER,madde TEXT,telaffuz TEXT,lisan TEXT,birlesikler TEXT);
        CREATE TABLE anlam(anlam_id INTEGER,madde_id INTEGER,anlam_sira INTEGER,anlam TEXT,fiil INTEGER);
        CREATE TABLE anlam_ozellik(anlam_id INTEGER,ozellik_id INTEGER);
        CREATE TABLE ozellik(ozellik_id INTEGER,tam_adi TEXT);
        CREATE TABLE ornek(ornek_id INTEGER,anlam_id INTEGER,ornek_sira INTEGER,ornek TEXT,yazar_id INTEGER,yazar_vd TEXT);
        CREATE TABLE yazar(yazar_id INTEGER,tam_adi TEXT);
        INSERT INTO madde VALUES(1,'ev',NULL,NULL,NULL),(2,'yazar',NULL,NULL,NULL),(3,'yazar',NULL,NULL,NULL);
        INSERT INTO anlam VALUES(1,1,1,'Konut.',0),(2,2,1,'Yazı yazan kimse.',0),(3,3,1,'Ayrı anlam.',0);
        INSERT INTO ozellik VALUES(1,'isim');
        INSERT INTO anlam_ozellik VALUES(1,1);
        INSERT INTO ornek VALUES(1,1,1,'Ev güzeldi.',99,'Anonim');
    ''')
    db.commit()
    db.close()
    source('tdk', 'tdk-sqlite-v1', 'tdk.db', 'tr', 'tr-tr')
    with zipfile.ZipFile(root / 'tureng.zip', 'w') as z:
        z.writestr('tureng_database.json', json.dumps({'tur': {'ev': ['house', 'home', 'house'], 'ev işi': ['housework']}, 'ing': {'ignored': ['ignore']}}))
    source('tureng', 'tureng-v1', 'tureng.zip', 'en', 'tr-en')
    with zipfile.ZipFile(root / 'wty.zip', 'w') as z:
        z.writestr('index.json', json.dumps(dict(format=3, sourceLanguage='tr', targetLanguage='en', revision='fixture')))
        z.writestr('term_bank_1.json', json.dumps([
            ['ev','','noun','',0,['dwelling'],1,''],
            ['evler','','non-lemma','',0,[['ev','plural']],2,''],
            ['karma','','noun','',0,['mixture',['karmak','form']],3,''],
            ['unresolved','','non-lemma','',0,[['nonexistent','form']],4,'']
        ]))
    source('wty', 'wty-v1', 'wty.zip', 'en', 'tr-en')
    (root / 'counts.txt').write_text("ev 30\nevler 20\nyazar 10\nhttp://bad 3\n", encoding='utf-8')
    source('subtitles', 'counts-v1', 'counts.txt', 'tr', 'frequency')
    write_json(root / 'sources.json', sources)
    config = dict(manifest='sources.json', cache='cache', frequency=dict(wordfreq=False, books=[]),
                  weights=dict(subtitles=1.0), morphology=dict(engine='fixture', observed_limit=10,
                  fixture_analyses={'evler': [dict(lemma='ev', features=[dict(id='A3pl', label='plural')], raw='fixture')]}))
    write_json(root / 'config.json', config)
    return config


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = fixtures(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def pipeline(self):
        return Pipeline(self.root, self.root / 'config.json')

    def test_independent_rebuild_and_cached_rebuild_are_identical(self):
        first = self.pipeline()
        first.run('build')
        second = self.pipeline()
        second.run('build')
        self.assertIn('normalize', second.hits)
        self.assertIn('validate', second.hits)
        self.config['cache'] = 'independent-cache'
        write_json(self.root / 'config.json', self.config)
        third = self.pipeline()
        third.run('build')
        self.assertEqual(digest(first.outputs['export'] / 'canonical.zip'), digest(third.outputs['export'] / 'canonical.zip'))
        self.assertTrue(read_json(third.outputs['validate'] / 'report.json')['valid'])
        with zipfile.ZipFile(third.outputs['export'] / 'canonical.zip') as z:
            self.assertTrue(all(i.date_time == (1980,1,1,0,0,0) for i in z.infolist()))

    def test_definitions_preserve_senses_homographs_and_redirects(self):
        out = self.pipeline().run('normalize')
        db = sqlite3.connect(out / 'definitions.sqlite3')
        try:
            self.assertEqual(db.execute("SELECT count(*) FROM entries WHERE key='yazar'").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT count(*) FROM entries WHERE key='evler'").fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM redirects').fetchone()[0], 3)
            senses = json.loads(db.execute("SELECT senses FROM entries WHERE id='tdk:1'").fetchone()[0])
            self.assertEqual(senses[0]['examples'][0]['author'], 'Anonim')
            senses = json.loads(db.execute("SELECT senses FROM entries WHERE id LIKE 'tureng:%' AND key='ev'").fetchone()[0])
            self.assertEqual(len(senses), 2)
            self.assertEqual(read_json(out / 'report.json')['unresolved_redirects'], 2)
        finally:
            db.close()

    def test_corpus_change_reuses_definitions_only(self):
        first = self.pipeline()
        first.run('build')
        (self.root / 'counts.txt').write_text('evler 40\nev 20\n', encoding='utf-8')
        sources = read_json(self.root / 'sources.json')
        sources[-1]['sha256'] = digest(self.root / 'counts.txt')
        write_json(self.root / 'sources.json', sources)
        second = self.pipeline()
        second.run('build')
        self.assertIn('normalize', second.hits)
        self.assertNotIn('frequency', second.hits)
        self.assertNotIn('analyze', second.hits)

    def test_reject_corrupt_source_and_partial_input(self):
        source = read_json(self.root / 'sources.json')[-1]
        with self.assertRaises(ValueError):
            verify(self.root / 'fake.part', source)
        (self.root / 'counts.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'Checksum'):
            self.pipeline().run('frequency')

    def test_corrupt_cache_rebuilds(self):
        first = self.pipeline().run('normalize')
        expected = digest(first / 'definitions.sqlite3')
        (first / 'definitions.sqlite3').write_bytes(b'corrupt')
        second = self.pipeline()
        out = second.run('normalize')
        self.assertNotIn('normalize', second.hits)
        self.assertEqual(expected, digest(out / 'definitions.sqlite3'))

    def test_frequency_noise_and_ties(self):
        out = self.pipeline().run('frequency')
        db = sqlite3.connect(out / 'frequency.sqlite3')
        try:
            self.assertEqual(list(ranked(db, {'subtitles': 1}))[0][0], 'ev')
            self.assertEqual(db.execute('SELECT count(*) FROM signals').fetchone()[0], 3)
        finally:
            db.close()

    def test_kenet_sense_groups_and_optional_source(self):
        (self.root / 'kenet.xml').write_text('''<SYNSETS>
          <SYNSET><ID>s1</ID><SYNONYM><LITERAL>ev<SENSE>2</SENSE></LITERAL>
          <LITERAL>konut<SENSE>1</SENSE></LITERAL></SYNONYM><POS>n</POS>
          <DEF>Barınılacak yer.</DEF><SR>s2<TYPE>HYPERNYM</TYPE></SR>
          <SR>missing<TYPE>ANTONYM</TYPE></SR></SYNSET>
          <SYNSET><ID>s2</ID><SYNONYM><LITERAL>yapı<SENSE>1</SENSE></LITERAL></SYNONYM>
          <POS>n</POS><DEF>İnşa edilmiş şey.</DEF></SYNSET></SYNSETS>''', encoding='utf-8')
        sources = read_json(self.root / 'sources.json')
        sources.append(dict(id='kenet', parser='kenet-v1', path='kenet.xml', sha256=digest(self.root / 'kenet.xml'),
                            snapshot='fixture', language='tr', direction='tr-tr', optional=True))
        write_json(self.root / 'sources.json', sources)
        out = self.pipeline().run('normalize')
        db = sqlite3.connect(out / 'definitions.sqlite3')
        try:
            sense = json.loads(db.execute("SELECT senses FROM entries WHERE id='kenet:s1:0'").fetchone()[0])[0]
            self.assertEqual(sense['member_sense'], '2')
            self.assertEqual([m['spelling'] for m in sense['synonyms']], ['ev', 'konut'])
            self.assertEqual(sense['relations'][0]['kind'], 'HYPERNYM')
            self.assertEqual(db.execute("SELECT count(*) FROM entries WHERE key='ev' AND language='tr'").fetchone()[0], 2)
            self.assertEqual(read_json(out / 'report.json')['kenet']['missing_relation_targets'], 1)
        finally:
            db.close()
        self.config['disabled_sources'] = ['kenet']
        write_json(self.root / 'config.json', self.config)
        self.assertNotIn('kenet', read_json(self.pipeline().run('normalize') / 'report.json'))

    def test_normalization(self):
        self.assertEqual(key('IŞIK'), 'ışık')
        self.assertEqual(key('I\u0307Z'), 'iz')
        self.assertEqual(key('Türkiye’nin'), "türkiye'nin")
        self.assertNotEqual(key('kâr'), key('kar'))
        for word in ['Türkiye’nin', 'kâğıt', 'ev', 'özgürlük']:
            self.assertTrue(valid_token(word))
        for word in ['a1', 'http://a', "'ev", 'a\u200bb', 'xx--']:
            self.assertFalse(valid_token(word))

    def test_epub_spine_and_content_identity(self):
        def make(path, identifier):
            with zipfile.ZipFile(path, 'w') as z:
                z.writestr('META-INF/container.xml', '<container><rootfile full-path="OPS/book.opf"/></container>')
                z.writestr('OPS/book.opf', f'<package xmlns="http://www.idpf.org/2007/opf"><metadata><identifier>{identifier}</identifier><title>Book</title></metadata><manifest><item id="c" href="chapter.xhtml"/></manifest><spine><itemref idref="c"/></spine></package>')
                z.writestr('OPS/chapter.xhtml', '<p>Ev ev.</p><p>Kitap.</p><script>noise</script>')
                z.writestr('OPS/navigation.xhtml', 'ignored')
        make(self.root / 'a.epub', 'a')
        make(self.root / 'b.epub', 'b')
        a, counts = epub(self.root / 'a.epub')
        b, _ = epub(self.root / 'b.epub')
        self.assertEqual(a['text_sha256'], b['text_sha256'])
        self.assertEqual(dict(counts), {'ev': 2, 'kitap': 1})
        self.config['frequency']['books'] = ['a.epub', 'b.epub']
        write_json(self.root / 'config.json', self.config)
        out = self.pipeline().run('frequency')
        db = sqlite3.connect(out / 'frequency.sqlite3')
        try:
            self.assertEqual(db.execute("SELECT value FROM signals WHERE surface='ev' AND source='book_occurrences'").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT value FROM signals WHERE surface='ev' AND source='book_documents'").fetchone()[0], 1)
        finally:
            db.close()


if __name__ == '__main__':
    unittest.main()
