import unittest

from scripts.separate_dictionaries import separated_rows
from scripts.export_koreader import bilingual_content


class SourceSeparationTests(unittest.TestCase):
    def test_sources_are_independent_and_kenet_retains_senses_without_synonyms(self):
        records = [
            ('tdk', [dict(text='TDK meaning')], 'word'),
            ('wty', [dict(text='Wiktionary meaning')], 'word'),
            ('tureng', [dict(text='Tureng translation')], 'word'),
            ('kenet', [dict(text='KeNet solitary meaning', synset_id='a', member_sense='1', synonyms=[dict(spelling='word')]),
                       dict(text='KeNet synonym meaning', synset_id='b', member_sense='2', synonyms=[dict(spelling='word'), dict(spelling='other')])], 'word'),
        ]
        rows = {pack['title']: row for pack, row in separated_rows('word', records, dict(visible_senses=3))}
        self.assertEqual(set(rows), {'TDK', 'Wiktionary', 'TR-EN Tureng', 'KeNet'})
        for title, row in rows.items():
            body = str(row[5])
            self.assertIn('Tureng' if title == 'TR-EN Tureng' else title, body)
            for other in set(rows) - {title}:
                self.assertNotIn(other, body)
        self.assertIn('KeNet solitary meaning', str(rows['KeNet'][5]))
        self.assertIn('KeNet synonym meaning', str(rows['KeNet'][5]))

    def test_standalone_tureng_keeps_overflow_accessible_in_koreader(self):
        records = [('tureng', [dict(text=f'translation{i}') for i in range(15)], 'word')]
        _, row = next(separated_rows('word', records, dict(visible_translations=10)))
        body, extra = bilingual_content(row[5], 'word')
        self.assertIn('bword://word (Tureng)', body)
        self.assertNotIn('translation14', body)
        self.assertIn('translation14', extra[1])


if __name__ == '__main__':
    unittest.main()
