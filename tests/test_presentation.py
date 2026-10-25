import unittest

from scripts.preview import content_text, source_content, morphology_rows, more_list
from scripts.export_prototype import structured, compact_alternatives, render_definition


class PresentationTests(unittest.TestCase):
    def test_kenet_title_and_smaller_unbolded_synonyms(self):
        records = [('kenet', [dict(text='Meaning', synonyms=[dict(spelling='x'), dict(spelling='y')])], 'x')]
        result = render_definition('x', 'tr', records, dict(source_order={'tr': ['kenet']}))
        self.assertIn('<h4>KeNet</h4>', result)
        self.assertNotIn('Synonyms', result)
        self.assertNotIn('<b>', result)
        self.assertIn('Meaning', result)
        self.assertIn("'fontSize': '0.8em'", str(structured(result)))

    def test_tureng_default_shows_ten_translations_before_more(self):
        result = source_content('tureng', [dict(text=f'word{i}') for i in range(13)], 'x', {})
        visible, overflow = result.split('<details>')
        self.assertIn('word9', visible)
        self.assertNotIn('word10', visible)
        self.assertIn('word12', overflow)

    def test_inline_boundaries_and_blocks(self):
        self.assertEqual(content_text(['Yüz', {'tag': 'span', 'content': 'ümü'}, ' yıkadım.']), 'Yüzümü yıkadım.')
        self.assertEqual(content_text([{'tag': 'div', 'content': 'One'}, {'tag': 'div', 'content': 'Two'}]), 'One\nTwo\n')

    def test_flat_translation_expansion_preserves_every_item(self):
        text = source_content('tureng', [dict(text=f'word{i}') for i in range(20)], 'x', dict(visible_translations=8))
        self.assertNotIn('word8;', text.split('<details>')[0])
        self.assertIn('word19', text)
        self.assertIn('<summary>More</summary>', text)
        self.assertNotIn('<ol', text)
        self.assertNotIn('translations', text)

    def test_synonyms_never_start_with_an_empty_more_section(self):
        senses = [dict(member_sense='8', text='A peripheral meaning', synset_id='a',
                       synonyms=[dict(spelling='x'), dict(spelling='y')])]
        result = source_content('kenet', senses, 'x', dict(visible_senses=3))
        self.assertNotIn('<details>', result)
        self.assertIn('A peripheral meaning', result)
        self.assertIn('y', result)

    def test_source_native_labels_and_readable_color(self):
        result = source_content('tdk', [dict(text='Definition', labels=['isim', 'argo'])], 'x', {})
        self.assertIn('argo', result)
        self.assertNotIn('isim', result)
        self.assertNotIn('#666', str(structured('<div class="examples">Example</div>')))

    def test_shared_grammar_and_explicit_alternatives(self):
        result = compact_alternatives([['x', ['clause', 'you', 'accusative']], ['x', ['clause', 'he', 'accusative']]])
        self.assertEqual(result, [['x', ['clause', '(you OR he)', 'accusative']]])

    def test_homograph_and_more_numbering_continue(self):
        result = more_list(['a', 'b', 'c', 'd'], 1, start=4, number_single=True)
        self.assertIn('4. a', result)
        self.assertIn('5. b', result)
        self.assertIn('7. d', result)
        self.assertNotIn('1.', result)
        self.assertIn('8. only', more_list(['only'], 1, start=8, number_single=True))

    def test_lexical_entries_have_headwords_and_independent_numbering(self):
        settings = dict(source_order={'tr': ['tdk'], 'en': ['wty']}, visible_senses=3)
        for source, language in [('tdk', 'tr'), ('wty', 'en')]:
            with self.subTest(source=source):
                records = [(source, [dict(text=text) for text in ['Universe', 'Whole', 'Snake', 'Environment']], 'evren'),
                           (source, [dict(text='District in Ankara')], 'Evren')]
                result = render_definition('evren', language, records, settings)
                first, second = result.split('<div class="entry-headword">Evren</div>')
                self.assertIn('<div class="entry-headword">evren</div>', first)
                self.assertIn('1. Universe', first)
                self.assertIn('<details><summary>More</summary>', first)
                self.assertIn('4. Environment', first)
                self.assertIn('</details>', first)
                self.assertIn('<div class="sense">District in Ankara</div>', second)
                self.assertNotIn('<details>', second)
                self.assertNotIn('5.', second)

    def test_same_spelling_homographs_remain_separate_and_restart_numbers(self):
        records = [('tdk', [dict(text=f'{prefix}{i}') for i in range(4)], 'x & y')
                   for prefix in ['a', 'b']]
        result = render_definition('x & y', 'tr', records, dict(source_order={'tr': ['tdk']}))
        self.assertEqual(result.count('<div class="entry-headword">x &amp; y</div>'), 2)
        self.assertEqual(result.count('<details>'), 2)
        self.assertIn('1. a0', result)
        self.assertIn('1. b0', result)
        self.assertNotIn('5.', result)
        self.assertIn('fontWeight', str(structured(result)))

    def test_clause_person_is_not_literal_ownership(self):
        record = dict(provenance=dict(raw='analysis'), features=[dict(id=t, label=t) for t in ['Verb', 'PastPart', 'P2sg', 'Acc']])
        result = morphology_rows([record])
        self.assertIn('subject: you', result)
        self.assertNotIn('your (possessive)', result)


if __name__ == '__main__':
    unittest.main()
