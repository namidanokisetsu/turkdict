import gzip
import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path
import zlib

from scripts.export_koreader import INDEX_QUERY, bilingual_content, dictzip, render
from scripts.export_prototype import render_definition, structured


class KOReaderExportTests(unittest.TestCase):
    def test_alias_order_preserves_yomitan_order_after_exact_headword(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE words(word BLOB, sortkey BLOB, offset INTEGER, size INTEGER, lemma TEXT, priority INTEGER)')
        word = 'ağlaştılar'.encode()
        for offset, lemma, priority in [(10, 'ağ', 1), (20, 'ağlaşmak', 0), (30, 'ağlaştılar', -1)]:
            db.execute('INSERT INTO words VALUES(?,?,?,?,?,?)', (word, word.lower(), offset, 1, lemma, priority))
        self.assertEqual([r[1] for r in db.execute(INDEX_QUERY)], [30, 20, 10])

    def test_source_labels_are_small_and_not_bold_and_synonym_size_survives(self):
        content = structured('<h4>KeNet</h4><div class="synonyms">word</div>')
        body = render(content)
        self.assertIn('class="source-label">KeNet', body)
        self.assertIn('font-size:0.8em', body)
        self.assertNotIn('<b>', body)

    def test_wiktionary_first_and_tureng_overflow_link_preserves_all_translations(self):
        records = [('tureng', [dict(text=f'word{i}') for i in range(20)], 'ünlü'),
                   ('wty', [dict(text='famous')], 'ünlü')]
        glossary = [structured(render_definition('ünlü', 'en', records,
            dict(source_order={'en': ['tureng', 'wty']}, visible_translations=8)))]
        body, extra = bilingual_content(glossary, 'ünlü')
        self.assertLess(body.index('Wiktionary'), body.index('Tureng'))
        self.assertIn('word7', body)
        self.assertNotIn('word8', body)
        self.assertNotIn('word19', body)
        self.assertIn('href="bword://ünlü (Tureng)">More</a>', body)
        self.assertEqual(extra[0], 'ünlü (Tureng)')
        self.assertIn('; '.join(f'word{i}' for i in range(20)), extra[1])

    def test_short_tureng_list_does_not_create_an_extra_entry(self):
        glossary = [structured(render_definition('ev', 'en',
            [('tureng', [dict(text='house'), dict(text='home')], 'ev')],
            dict(source_order={'en': ['tureng', 'wty']}, visible_translations=8)))]
        body, extra = bilingual_content(glossary, 'ev')
        self.assertIsNone(extra)
        self.assertIn('house; home', body)
        self.assertNotIn('More', body)

    def test_html_expands_all_senses_and_preserves_inline_text(self):
        content = {'type': 'structured-content', 'content': [
            {'tag': 'div', 'content': ['Yüz', {'tag': 'span', 'content': 'ümü'}, ' & <text>']},
            {'tag': 'details', 'content': [{'tag': 'summary', 'content': 'More'},
                                         {'tag': 'div', 'content': 'Last sense'}]}]}
        result = render(content)
        self.assertIn('Yüz<span>ümü</span> &amp; &lt;text&gt;', result)
        self.assertIn('Last sense', result)
        self.assertNotIn('details', result)
        self.assertNotIn('More', result)

    def test_dictzip_crc_and_random_chunk_access_are_valid(self):
        payload = ('evde · İstanbul · evlerimizden\n' * 12000).encode()
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'test.dict'
            dest = Path(tmp) / 'test.dict.dz'
            source.write_bytes(payload)
            dictzip(source, dest)
            self.assertEqual(gzip.decompress(dest.read_bytes()), payload)
            data = dest.read_bytes()
            extra_size = struct.unpack_from('<H', data, 10)[0]
            self.assertEqual(data[12:14], b'RA')
            version, chunk_size, count = struct.unpack_from('<HHH', data, 16)
            self.assertEqual(version, 1)
            sizes = struct.unpack_from('<' + 'H' * count, data, 22)
            offset = 12 + extra_size
            for i, size in enumerate(sizes):
                decoded = zlib.decompressobj(-15).decompress(data[offset:offset+size])
                self.assertEqual(decoded, payload[i*chunk_size:(i+1)*chunk_size])
                offset += size
            first = data
            dictzip(source, dest)
            self.assertEqual(first, dest.read_bytes())


if __name__ == '__main__':
    unittest.main()
