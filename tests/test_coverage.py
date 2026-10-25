import sqlite3
import unittest

from scripts.audit_coverage import audit


class CoverageTests(unittest.TestCase):
    def test_source_duplicates_ambiguity_languages_and_complete_failures(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE entries(id TEXT, key TEXT, language TEXT)')
        db.executemany('INSERT INTO entries VALUES(?,?,?)',
                       [('tdk:1', 'ev', 'tr'), ('kenet:1', 'ev', 'tr'),
                        ('tureng:1', 'ev', 'en'), ('tdk:2', 'özel', 'tr')])
        def mapping(surface, target, language, tags, evidence='engine-analysis', origin='observed'):
            return dict(surface=surface, target_lemma_id=target, definition_language=language,
                        features=[dict(id=t) for t in tags], evidence=evidence, origin=origin)
        rows = [mapping('evleri', target, lang, tags)
                for target, lang in [('tdk:1', 'tr'), ('kenet:1', 'tr'), ('tureng:1', 'en')]
                for tags in [('A3pl', 'Acc'), ('P3pl',)]]
        rows.append(mapping('özel', 'tdk:2', 'tr', ['Lexical'], 'dictionary-attested'))
        # A generated link for an observed-but-unlinked form is not a generated-only spelling.
        rows.append(mapping('missing', 'tdk:1', 'tr', ['Dat'], origin='generated'))
        rows.append(mapping('eve', 'tdk:1', 'tr', ['Dat'], origin='generated'))
        analyses = {'evleri': [dict(lemma='ev', features=[dict(id=t) for t in tags])
                               for tags in [('A3pl', 'Acc'), ('P3pl',)]],
                    'missing': [dict(lemma='absent', features=[dict(id='Noun')])],
                    'özel': []}
        words = ['evleri', 'özel', 'missing'] + [f'noise{i}' for i in range(20)]
        report, failures = audit(db, words, rows, lambda word: analyses.get(word, []))
        self.assertEqual(report['linked_observed_forms'], 2)
        self.assertEqual(report['observed_mapping_records'], 7)
        self.assertEqual(report['observed_surface_definition_id_pairs'], 4)
        self.assertEqual(report['observed_surface_lemma_spelling_pairs'], 2)
        self.assertEqual(report['observed_distinct_feature_sequences'], 3)
        self.assertEqual(report['ambiguous_linked_engine_forms'], 1)
        self.assertEqual(report['multiple_linked_lemma_forms'], 0)
        self.assertEqual(report['languages']['tr']['linked_forms'], 2)
        self.assertEqual(report['languages']['en']['linked_forms'], 1)
        self.assertEqual(report['no_analysis_but_exact_definition'], 1)
        self.assertEqual(report['exact_definition_only_forms'], 1)
        self.assertEqual(report['unlinked_with_analysis'], 1)
        self.assertEqual(report['unlinked_without_analysis'], 20)
        self.assertEqual(report['generated_only_surfaces'], 1)
        self.assertEqual(len(failures), 21)
        self.assertEqual(len(report['examples']['unlinked_without_analysis']), 15)

    def test_mismatched_mapping_rejected(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE entries(id TEXT, key TEXT, language TEXT)')
        db.execute("INSERT INTO entries VALUES('tdk:1','ev','tr')")
        row = dict(surface='ev', origin='observed', target_lemma_id='tdk:1', definition_language='en')
        with self.assertRaisesRegex(ValueError, 'wrong-language'):
            audit(db, ['ev'], [row], lambda _: [])
        with self.assertRaisesRegex(ValueError, 'outside'):
            audit(db, ['different'], [row], lambda _: [])


if __name__ == '__main__':
    unittest.main()
