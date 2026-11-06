/**
 * The public edition's vocabulary (5.4.0).
 *
 * The team edition's screens speak the vocabulary of the project the tool was
 * built for (NER, TER, TEAP, VDB, DIVA). The public edition must not: every
 * piece of interface copy goes through `t()`, and `t()` passes its result
 * through `publicTerms` here. Interface copy holds no data, so the rack names
 * of its examples are renamed too (NERc_01.1 → RKc_01.1, 5.5.0); what the
 * server writes is worded on the server (`app/public_words.py`), which leaves
 * a rack's name alone.
 */

type Replacer = string | ((substring: string, ...args: any[]) => string);
type Rules = { phrases: [string, string][]; words: Record<string, string>; articles: [RegExp, Replacer][] };

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
    // 5.5.0: codes of the project the tool was built for, never shown in public.
    ["as the AAM or the VDB writes it (TEC.010-001)", "as the design book writes it"],
    ["(AAM / design book)", "(design book)"],
    ["(AAM / VDB)", "(design book)"],
    ["the coded rooms — TEC.010 (critical) / TEC.020 (non-critical), or TEC.003.01 / .02 on older books —", "the rooms the book codes as critical or non-critical racks —"],
    ["the TEC.010 / TEC.020 (or TEC.003.01 / .02) rooms", "the rooms the book codes as racks"],
    ["a DWISA line", "a wireless line"],
    ["TEC.003 / TEC.010 / TEC.020 rooms", "rooms the book codes as racks"],
  ],
  words: {
    NERc: "critical rack", NERs: "racks", NER: "rack",
    TERs: "equipment rooms", TER: "equipment room",
    TEAPs: "demand lists", TEAP: "demand list",
    VDBs: "design books", VDB: "design book",
    DIVA: "AV network",
    VTMs: "technology managers", VTCMs: "technology managers",
    VTM: "technology manager", VTCM: "technology manager",
    DWISA: "wireless", AAM: "space programme",
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
    ["como o AAM ou o VDB escreve (TEC.010-001)", "como o caderno de design escreve"],
    ["(AAM / caderno de design)", "(caderno de design)"],
    ["(AAM / VDB)", "(caderno de design)"],
    ["as salas com código — TEC.010 (crítica) / TEC.020 (não crítica), ou TEC.003.01 / .02 nos livros antigos —", "as salas que o caderno codifica como racks críticos ou não críticos —"],
    ["as salas TEC.010 / TEC.020 (ou TEC.003.01 / .02)", "as salas que o caderno codifica como racks"],
    ["espaços TEAP", "espaços do levantamento de demanda"],
    ["espaço TEAP", "espaço do levantamento de demanda"],
    ["linha TEAP", "linha do levantamento de demanda"],
    ["linha DWISA", "linha wireless"],
  ],
  words: {
    NERc: "rack crítico", NERs: "racks", NER: "rack",
    TERs: "CPDs", TER: "CPD",
    TEAPs: "levantamentos de demanda", TEAP: "levantamento de demanda",
    VDBs: "cadernos de design", VDB: "caderno de design",
    DIVA: "rede AV",
    VTMs: "gestores de tecnologia", VTCMs: "gestores de tecnologia",
    VTM: "gestor de tecnologia", VTCM: "gestor de tecnologia",
    DWISA: "wireless", AAM: "programa de espaços",
  },
  articles: [
    [/\b([Oo]) rede AV/g, "a rede AV"],
    [/\bdo rede AV/g, "da rede AV"],
    [/\bno rede AV/g, "na rede AV"],
    // "NER" is feminine in Portuguese, "rack" masculine (5.5.0).
    [/\b([Aa])s (racks?)\b/g, (_m: string, a: string, r: string) => (a === "A" ? "Os " : "os ") + r],
    [/\b([Aa]) (racks?)\b/g, (_m: string, a: string, r: string) => (a === "A" ? "O " : "o ") + r],
    [/\b([Uu])ma (rack)\b/g, (_m: string, u: string, r: string) => (u === "U" ? "Um " : "um ") + r],
    [/\b([DdNnPp])as (racks)\b/g, "$1os $2"],
    [/\b([DdNn])a (rack)\b/g, "$1o $2"],
    [/\b([Pp])ela (rack)\b/g, "$1elo $2"],
    [/\b([Pp])elas (racks)\b/g, "$1elos $2"],
    [/\bà (rack)\b/g, "ao $1"],
    [/\bàs (racks)\b/g, "aos $1"],
    [/\b([Nn])enhuma (rack)\b/g, "$1enhum $2"],
    [/\b([Cc])ada (rack) (crítica)\b/g, "$1ada $2 crítico"],
    [/\b([Rr]ack) (crítica)\b/g, "$1 crítico"],
    [/\b(racks) (críticas)\b/g, "$1 críticos"],
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
  for (const [rx2, rep] of r.articles) s = s.replace(rx2, rep as any);
  return upper ? s.toUpperCase() : s;
}

/** Rack names in examples: NERc_01.1 / NER_20.1 read RKc_01.1 / RK_20.1 (5.5.0). */
function names(s: string): string {
  return s.replace(/(?<![A-Za-z0-9])NERc_(\d)/g, "RKc_$1").replace(/(?<![A-Za-z0-9])NER_(\d)/g, "RK_$1")
    .replace(/(?<![A-Za-z_])NERc(\d)/g, "RKc$1").replace(/(?<![A-Za-z_])NER(\d)/g, "RK$1");
}

export function publicTerms(s: string, lang: "en" | "pt"): string {
  if (!s || typeof s !== "string") return s;
  return apply(names(s), lang === "pt" ? PT : EN);
}
