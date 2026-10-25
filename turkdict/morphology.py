"""Offline adapters preserve alternative analyses and engine-native feature order."""
import importlib.metadata
import os
from pathlib import Path
import re
import subprocess

from .common import digest, key, read_json

LABELS = {
    'Noun': 'noun', 'Verb': 'verb', 'Adj': 'adjective', 'Adv': 'adverb',
    'Pron': 'pronoun', 'Prop': 'proper name', 'Neg': 'negation', 'Pos': 'positive',
    'Past': 'past tense', 'Narr': 'reported past', 'Fut': 'future',
    'Prog1': 'progressive', 'Prog2': 'progressive', 'Aor': 'aorist',
    'Imp': 'imperative', 'Opt': 'optative', 'Desr': 'desiderative', 'Cond': 'conditional',
    'Neces': 'necessity', 'Able': 'ability', 'Unable': 'inability',
    'Pass': 'passive', 'Caus': 'causative', 'Recip': 'reciprocal', 'Reflex': 'reflexive',
    'Acc': 'accusative', 'Dat': 'dative', 'Loc': 'locative', 'Abl': 'ablative',
    'Gen': 'genitive', 'Ins': 'instrumental', 'Nom': 'nominative', 'Pnon': 'no possessor',
    'Zero': 'zero derivation', 'Inf1': 'infinitive', 'Inf2': 'verbal noun',
    'PastPart': 'past participle', 'FutPart': 'future participle', 'PresPart': 'present participle',
    'With': 'having', 'Without': 'without', 'Agent': 'agent noun', 'Ness': 'abstract noun',
    'DB': 'derivation boundary', 'Cop': 'copula',
    'Pres': 'present tense', 'Num': 'numeral', 'AorPart': 'aorist participle',
    'Interj': 'interjection', 'Agt': 'agent noun', 'Rel': 'relative form',
    'NarrPart': 'reported past participle', 'Acquire': 'acquire or develop',
    'Postp': 'postposition', 'Equ': 'equative', 'Conj': 'conjunction',
    'While': 'while doing', 'Inf3': 'verbal noun in -iş', 'Become': 'become',
    'AfterDoingSo': 'after doing', 'AsIf': 'as if', 'Ly': 'adverb derivation',
    'Related': 'related to', 'Det': 'determiner', 'Ques': 'question particle',
    'ByDoingSo': 'by doing', 'WithoutHavingDoneSo': 'without doing',
    'JustLike': 'like or resembling', 'FeelLike': 'feeling like doing',
    'Dim': 'diminutive', 'When': 'when doing', 'AsLongAs': 'as long as',
    'SinceDoingSo': 'since doing', 'Dup': 'reduplication',
}
for person in range(1, 4):
    for number, text in [('sg', 'singular'), ('pl', 'plural')]:
        LABELS[f'A{person}{number}'] = f'{person}. person {text} agreement'
        LABELS[f'P{person}{number}'] = f'{person}. person {text} possessor'
ALIASES = {k.upper(): k for k in LABELS}
ALIASES.update(PROG1='Prog1', PROG2='Prog2', NECESSITY='Neces', ABLE='Able',
               POSITIVE='Pos', NEGATIVE='Neg', ADVERB='Adv', ADJECTIVE='Adj',
               INFINITIVE='Inf1', INFINITIVE2='Inf2', DESIRE='Desr')


def features(tags):
    out = []
    pos = None
    for tag in tags:
        tag = ALIASES.get(tag.upper(), tag)
        if tag in ('Noun', 'Verb', 'Adj', 'Adv', 'Pron'):
            pos = tag
        label = LABELS.get(tag, f'Engine tag: {tag}')
        if pos == 'Noun' and tag in ('A3sg', 'A3pl'):
            label = 'singular noun' if tag == 'A3sg' else 'plural noun'
        out.append(dict(id=tag, label=label))
    return out


class Dilbaz:
    def __init__(self, root=None):
        from MorphologicalAnalysis.FsmMorphologicalAnalyzer import FsmMorphologicalAnalyzer
        self.engine = FsmMorphologicalAnalyzer()
        self.identity = 'dilbaz:' + importlib.metadata.version('NlpToolkit-MorphologicalAnalysis')

    def analyze(self, surface):
        parses = self.engine.morphologicalAnalysis(surface.replace('’', "'"))
        out = []
        for i in range(parses.size()):
            p = parses.getFsmParse(i)
            raw = p.transitionList()
            lemma = p.getWord().getName()
            if p.getRootPos() == 'VERB':
                vowels = [v for v in key(lemma) if v in 'aeıioöuü']
                lemma += 'mak' if vowels and vowels[-1] in 'aıou' else 'mek'
            tags = re.split(r'\+|\^', raw)[1:]
            out.append(dict(lemma=key(lemma), features=features(tags), raw=raw, segmentation=None))
        return out

    def close(self):
        pass


class Zemberek:
    def __init__(self, root):
        root = Path(root)
        for item in read_json(root / 'config/java.lock.json'):
            if digest(root / item['path']) != item['sha256']:
                raise ValueError('Java artifact checksum mismatch')
        java = sorted((root / 'tools').glob('jdk-*/bin/java.exe'))
        if len(java) != 1:
            raise ValueError('Run scripts/setup_java.py (one pinned JDK required)')
        java = java[0]
        classes = root / 'cache/java'
        classes.mkdir(parents=True, exist_ok=True)
        cp = str(root / 'tools/*')
        subprocess.run([str(java.with_name('javac.exe')), '-encoding', 'UTF-8', '-cp', cp,
                        '-d', str(classes), str(root / 'scripts/MorphologyBridge.java')], check=True, capture_output=True)
        self.process = subprocess.Popen([str(java), '-Dfile.encoding=UTF-8', '-cp', cp + os.pathsep + str(classes),
                                         'MorphologyBridge'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, encoding='utf-8', bufsize=1)
        if self.process.stdout.readline().strip() != 'READY':
            self.close()
            raise RuntimeError('Zemberek failed to start')
        self.identity = 'zemberek:0.17.1'

    def request(self, line):
        if '\n' in line or '\r' in line:
            raise ValueError('Invalid line protocol input')
        self.process.stdin.write(line + '\n')
        self.process.stdin.flush()
        out = []
        while True:
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError('Zemberek bridge exited')
            if line == '\n':
                break
            surface, lemma, tags, raw = line.rstrip('\n').split('\t', 3)
            out.append(dict(surface=surface, lemma=key(lemma), features=features(tags.split(',')),
                            raw=raw, segmentation=None))
        return out

    def analyze(self, surface):
        return self.request('A\t' + surface.replace('’', "'"))

    def generate(self, lemma, tags):
        return self.request('G\t' + lemma + '\t' + ','.join(tags))

    def close(self):
        if getattr(self, 'process', None):
            self.process.stdin.close()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.process.stdout.close()


def create(name, root):
    return {'dilbaz': Dilbaz, 'zemberek': Zemberek}[name](root)
