import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import zemberek.morphology.TurkishMorphology;
import zemberek.morphology.analysis.SingleAnalysis;
import zemberek.morphology.lexicon.DictionaryItem;
import zemberek.morphology.generator.WordGenerator;

// Line protocol: input A<TAB>surface or G<TAB>lemma<TAB>morpheme IDs joined by commas.
// Output: surface, dictionary lemma, ordered morphemes, full analysis; blank line terminates.
class MorphologyBridge {
    static boolean permittedGeneration(SingleAnalysis a, String[] requested) {
        // The generator can traverse zero derivation into a copular verb even for A3pl.
        // Keep only the requested noun path, including implicit zero-surface defaults.
        Set<String> allowed = new HashSet<>(Arrays.asList("Noun", "A3sg", "Pnon", "Nom"));
        allowed.addAll(Arrays.asList(requested));
        List<String> actual = new ArrayList<>();
        for (var morpheme : a.getMorphemes()) {
            if (!allowed.contains(morpheme.id)) return false;
            if (Arrays.asList(requested).contains(morpheme.id)) actual.add(morpheme.id);
        }
        return actual.equals(Arrays.asList(requested));
    }
    static void emit(String surface, SingleAnalysis a) {
        StringJoiner tags = new StringJoiner(",");
        a.getMorphemes().forEach(m -> tags.add(m.id));
        System.out.println(surface + "\t" + a.getDictionaryItem().lemma + "\t" + tags + "\t" + a.formatLong());
    }
    public static void main(String[] args) throws Exception {
        System.setOut(new PrintStream(System.out, true, StandardCharsets.UTF_8));
        TurkishMorphology m = TurkishMorphology.createWithDefaults();
        BufferedReader input = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        System.out.println("READY"); System.out.flush();
        String line;
        while ((line = input.readLine()) != null) {
            String[] parts = line.split("\t", -1);
            if (parts[0].equals("A")) {
                for (SingleAnalysis a : m.analyze(parts[1])) if (!a.isUnknown()) emit(parts[1], a);
            } else if (parts[0].equals("G")) {
                for (DictionaryItem item : m.getLexicon()) {
                    if (item.lemma.equals(parts[1])) {
                        String[] requested = parts[2].split(",");
                        for (WordGenerator.Result r : m.getWordGenerator().generate(item, requested)) {
                            if (permittedGeneration(r.analysis, requested)) emit(r.surface, r.analysis);
                        }
                    }
                }
            }
            System.out.println(); System.out.flush();
        }
    }
}
