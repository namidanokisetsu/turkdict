import unittest

from scripts.export_koreader_morphology import chain_labels, dictionary_paths, render_morphology


class MorphologyExportTests(unittest.TestCase):
    def test_source_precedence_keeps_all_dictionary_only_readings(self):
        import json
        rows = [('form', 'lemma', json.dumps({'raw': ['lemma', [label]]}))
                for label in ('accusative', 'possessive')]
        self.assertEqual([row[2:] for row in dictionary_paths(rows, lambda *_: False)],
                         [(['accusative'], False), (['possessive'], False)])
        self.assertEqual([row[3] for row in dictionary_paths(rows, lambda *_: True)], [True, True])

    def test_shared_grammar_preserves_correlated_readings_and_lemma_groups(self):
        body = render_morphology('kitabı', [('kitap', [
            ['noun', 'singular', 'accusative'],
            ['noun', 'singular', 'his / her / its (possessive)', 'nominative']]),
            ('other', [['adjective']])])
        self.assertEqual(body.count('class="grammar"'), 2)
        self.assertIn('noun · sg. · (accusative or his/her/its (poss.) · nominative)', body)
        self.assertIn('bword://kitap', body)
        self.assertNotIn('noun · sg. · his/her/its', body)

    def test_feature_order_defaults_and_repeated_derivation_survive(self):
        features = [dict(id=tag, label=label) for tag, label in [
            ('Verb', 'verb'), ('Caus', 'causative'), ('Caus', 'causative'),
            ('Pos', 'positive'), ('Past', 'past tense'), ('A3sg', 'third-person singular')]]
        self.assertEqual(chain_labels(features), ['verb', 'causative (make / have)',
            'causative (make / have)', 'positive', 'past', 'he / she / it'])
        self.assertEqual(chain_labels([dict(id='Noun', label='noun'),
            dict(id='A3sg', label='third-person singular'), dict(id='Nom', label='nominative')]),
            ['noun', 'singular', 'nominative'])

    def test_source_text_and_link_targets_are_escaped(self):
        body = render_morphology('<form>', [('a"&b', [['<label>', 'x & y']])])
        self.assertIn('&lt;form&gt;', body)
        self.assertIn('bword://a&quot;&amp;b', body)
        self.assertIn('&lt;label&gt; · x &amp; y', body)


if __name__ == '__main__':
    unittest.main()
