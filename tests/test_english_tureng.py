import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.add_english_tureng import english_entries, english_key, english_row, rename_dictionary
from scripts.export_koreader import bilingual_content
from scripts.export_prototype import archive


class EnglishTurengTests(unittest.TestCase):
    def test_english_case_and_apostrophes_preserve_translation_strings(self):
        entries, counts = english_entries({
            'I': ['ben', 'İngiliz alfabesinin dokuzuncu harfi'],
            'i': ['ben', 'iyodun simgesi'],
            ' Children’s book ': ['çocuk kitabı'],
            'empty': [' '],
        })
        self.assertEqual(english_key('INTERNET'), 'internet')
        self.assertEqual(list(entries['i']), ['ben', 'İngiliz alfabesinin dokuzuncu harfi', 'iyodun simgesi'])
        self.assertEqual(list(entries["children's book"]), ['çocuk kitabı'])
        self.assertEqual(counts['rejected_records'], 1)
        self.assertNotIn('ı', entries)

    def test_english_direction_and_all_overflow_translations_survive_both_formats(self):
        meanings = ['ev', 'konut'] + [f'çeviri {i}' for i in range(12)]
        row = english_row('house', meanings, dict(visible_translations=10))
        body, extra = bilingual_content(row[5], 'house')
        self.assertIn('bword://house (Tureng)', body)
        self.assertIn('çeviri 11', extra[1])
        with tempfile.TemporaryDirectory() as folder:
            source, renamed = Path(folder)/'source.zip', Path(folder)/'renamed.zip'
            archive(source, 'EN-TR Tureng', [row], 'fixture',
                    index_metadata={'sourceLanguage': 'en', 'targetLanguage': 'tr'})
            rename_dictionary(source, renamed, 'Renamed')
            with zipfile.ZipFile(source) as old, zipfile.ZipFile(renamed) as new:
                metadata = json.loads(new.read('index.json'))
                self.assertEqual((metadata['sourceLanguage'], metadata['targetLanguage']), ('en', 'tr'))
                self.assertEqual(metadata['title'], 'Renamed')
                self.assertEqual(old.read('term_bank_1.json'), new.read('term_bank_1.json'))
                self.assertIn('çeviri 11', new.read('term_bank_1.json').decode('utf-8'))


if __name__ == '__main__':
    unittest.main()
