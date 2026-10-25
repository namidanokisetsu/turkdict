import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile

from scripts.export_frequency import frequency_rows, read_frequencies
from scripts.export_prototype import archive
from scripts.export_koreader import frequency_header
from turkdict.presentation import compact_chain, compact_label, factor_paths


class FrequencyTests(unittest.TestCase):
    def test_global_ranks_survive_filtering_and_duplicate_senses(self):
        with sqlite3.connect(':memory:') as db:
            db.executescript('''ATTACH DATABASE ':memory:' AS definitions;
                CREATE TABLE definitions.entries(key TEXT);
                CREATE TABLE candidates(surface TEXT,rank INTEGER);
                INSERT INTO definitions.entries VALUES ('ev'),('ev'),('kitap'),('missing');
                INSERT INTO candidates VALUES ('bir',1),('ev',304),('kitap',592);''')
            rows = list(frequency_rows(db))
        self.assertEqual(rows, [['ev', 'freq', 304], ['kitap', 'freq', 592]])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'frequency.zip'
            archive(path, 'Frequency', rows, bank_prefix='term_meta_bank',
                    index_metadata={'frequencyMode': 'rank-based'})
            with zipfile.ZipFile(path) as z:
                self.assertEqual(json.loads(z.read('index.json'))['frequencyMode'], 'rank-based')
                self.assertNotIn('term_bank_1.json', z.namelist())
            self.assertEqual(read_frequencies(path), {'ev': 304, 'kitap': 592})
        self.assertIn('Frequency: 304', frequency_header('ev', {'ev': 304}))
        self.assertEqual(frequency_header('missing', {'ev': 304}), '')

    def test_factoring_is_reversible_without_inventing_combinations(self):
        paths = [['noun', 'my', 'accusative'], ['noun', 'your', 'dative']]
        prefix, middles, suffix = factor_paths(paths)
        self.assertEqual([prefix + middle + suffix for middle in middles], paths)
        self.assertEqual(compact_chain(paths), 'noun · (my · accusative or your · dative)')
        self.assertEqual(compact_chain([['past'], ['past', 'I']]), 'past · (unmarked or I)')
        self.assertEqual(compact_label('subject: he / she / it'), 'subject: he/she/it')
        self.assertEqual(compact_label('his / her / its (possessive)'), 'his/her/its (poss.)')
        self.assertEqual(compact_label('present continuous'), 'present continuous')
        self.assertEqual(compact_label('dialectal Isparta'), 'dialectal Isparta')


if __name__ == '__main__':
    unittest.main()
