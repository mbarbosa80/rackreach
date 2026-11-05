/**
 * The public edition's vocabulary (5.4.0).
 *
 * The team edition's screens speak the vocabulary of the project the tool was
 * built for (NER, TER, TEAP, VDB, DIVA). The public edition must not: every
 * piece of interface copy goes through `t()`, and `t()` passes its result
 * through `publicTerms` here. Rack names joined by an underscore (`RKc_01.1`)
 * are never touched — a word boundary is needed on both sides.
 */

type Rules = { phrases: [string, string][]; words: Record<string, string>; articles: [RegExp, string][] };

const EN: Rules = {
  phrases: [
    ["NER Spatial Coverage Analyzer", "RackReach"],
    ["NER Standardized Naming Structure", "rack naming structure"],
    ["Network Equipment Rack (NER)", "Network Equipment Rack"],
    ["network equipment rack (NER)", "network rack"],
    ["TEAP × drawing", "Demand × drawing"],
    ["TEAP x drawing", "Demand x drawing"],
    ["TEAP by NER", "Demand by rack"],
    ["NERs and ports", "Racks and ports"],
    ["TEAP and coverage", "Demand and coverage"],
    ["TEAP spaces with no ports", "Demand spaces with no ports"],
    ["TEAP files", "Demand files"],
  ],
  words: {
    NERc: "critical rack", NERs: "racks", NER: "rack",
    TERs: "equipment rooms", TER: "equipment room",
    TEAPs: "demand lists", TEAP: "demand list",
    VDBs: "design books", VDB: "design book",
    DIVA: "AV network",
    VTMs: "technology managers", VTCMs: "technology managers",
    VTM: "technology manager", VTCM: "technology manager",
  },
  articles: [
    [/\b([Aa]) (equipment room|AV network)/g, "$1n $2"],
    [/\b([Aa])n (rack|critical rack|demand list|design book|technology manager)/g, "$1 $2"],
  ],
};

const PT: Rules = {
  phrases: [
    ["NER Spatial Coverage Analyzer", "RackReach"],
    ["Network Equipment Rack (NER)", "Network Equipment Rack"],
    ["TEAP × drawing", "Demanda × desenho"],
    ["TEAP × desenho", "Demanda × desenho"],
    ["TEAP by NER", "Demanda por rack"],
  ],
  words: {
    NERc: "rack crítico", NERs: "racks", NER: "rack",
    TERs: "CPDs", TER: "CPD",
    TEAPs: "levantamentos de demanda", TEAP: "levantamento de demanda",
    VDBs: "cadernos de design", VDB: "caderno de design",
    DIVA: "rede AV",
    VTMs: "gestores de tecnologia", VTCMs: "gestores de tecnologia",
    VTM: "gestor de tecnologia", VTCM: "gestor de tecnologia",
  },
  articles: [
    [/\b([Oo]) rede AV/g, "a rede AV"],
    [/\bdo rede AV/g, "da rede AV"],
    [/\bno rede AV/g, "na rede AV"],
  ],
};

const SENTENCE_START = /(?:^|[.!?:]\s+|\n\s*)$/;

const ACRONYMS = new Set(["XLSX", "XLSM", "XLS", "CSV", "PDF", "IDF", "MDF", "COW", "AP", "LLD", "URL", "OK", "ID"]);

/** A title written in capitals ("NER COVERAGE"), not a label that merely holds acronyms. */
function shouting(s: string, r: Rules): boolean {
  const words = (s.match(/[A-Za-z]+/g) || []).filter((w) => !(w in r.words));
  return words.length > 0 && words.every((w) => w === w.toUpperCase()) && words.some((w) => !ACRONYMS.has(w));
}

function apply(s: string, r: Rules): string {
  const upper = shouting(s, r);
  for (const [a, b] of r.phrases) s = s.split(a).join(b);
  const keys = Object.keys(r.words).sort((a, b) => b.length - a.length);
  const rx = new RegExp(`\\b(${keys.join("|")})\\b`, "g");
  s = s.replace(rx, (m: string, w: string, offset: number, whole: string) => {
    let out = r.words[w];
    const prefix = whole.slice(0, offset);
    if (!/[A-Za-z]/.test(prefix) || SENTENCE_START.test(prefix)) out = out[0].toUpperCase() + out.slice(1);
    return out;
  });
  for (const [rx2, rep] of r.articles) s = s.replace(rx2, rep);
  return upper ? s.toUpperCase() : s;
}

export function publicTerms(s: string, lang: "en" | "pt"): string {
  if (!s || typeof s !== "string") return s;
  return apply(s, lang === "pt" ? PT : EN);
}
