# Protein Friend Finder — Iterative Crawl Workflow: Implementation Spec

**Status:** ready to implement
**Scope:** backend rewrite of the collection pipeline + graph builder, plus the frontend wiring needed to drive it
**Audience:** any engineer or AI agent with no prior context on this repository. Every claim about existing code was verified by reading it. Every external API claim was verified by calling it.

---

## Table of contents

1. [Decisions locked (read first)](#1-decisions-locked-read-first)
2. [What the code does today](#2-what-the-code-does-today)
3. [Gap analysis, mapped to the four workflow steps](#3-gap-analysis-mapped-to-the-four-workflow-steps)
4. [Target workflow](#4-target-workflow)
5. [Hard rules that must not be broken](#5-hard-rules-that-must-not-be-broken)
6. [Data model changes](#6-data-model-changes)
7. [SQLite schema](#7-sqlite-schema)
8. [New modules](#8-new-modules)
9. [Modified modules — exact changes](#9-modified-modules--exact-changes)
10. [API contract](#10-api-contract)
11. [Frontend changes](#11-frontend-changes)
12. [Environment variables](#12-environment-variables)
13. [Implementation order](#13-implementation-order)
14. [Acceptance criteria](#14-acceptance-criteria)
15. [Deviations, risks and dead ends](#15-deviations-risks-and-dead-ends)

---

## 1. Decisions locked (read first)

These were resolved with the requester. Do not re-litigate them; implement what is written.

| # | Decision | Choice |
|---|---|---|
| D1 | Paper ranking | **Keep PubMed relevance sort.** No citation sort. See [§15.1](#151-known-deviation-most-cited-is-not-most-cited) — this is a deliberate, accepted deviation from the original "50 most cited" wording. |
| D2 | Full text | **Abstract for every paper; full text only for the top N most-cited/relevant papers.** Default `N = 10`. |
| D3 | Full text source | **NCBI PMC via `Entrez.efetch(db="pmc")`.** No Europe PMC anywhere. Verified working: 121,767 bytes of JATS XML for `PMC3148502`, contains `<body>`. |
| D4 | Interaction source | **LLM extraction only.** No IntAct / PSICQUIC / STRING / BioGRID. |
| D5 | Cross-organism proteins | **One node per ortholog cluster.** Human reference is the node label; every species' alias maps to that node; every edge records the taxid of the evidence. |
| D6 | Interaction vocabulary | **Keep the 4 display types** (`physical_binding`, `regulatory`, `complex_formation`, `genetic`) **and add a `subtypes` detail field** carrying `activation`, `inhibition`, `phosphorylation`, `dephosphorylation`, `ubiquitination`, `deubiquitination`, `sumoylation`, `acetylation`, `methylation`, `glycosylation`, `cleavage`, `transcriptional_regulation`, `post_translational_regulation`. No information is lost; the UI filter list stays at 4. |
| D7 | Directionality | **A first-class `directed` boolean.** `source_protein` is always the actor when `directed` is true. |
| D8 | Crawl budget | **Configurable caps with safe defaults**, every cap reported in the response so nothing is silently truncated. |
| D9 | Execution model | **Background job + SQLite cache.** |
| D10 | Graph construction | **Only directly-evidenced edges.** Never derive `A→C` from `A→B→C`. A directly-stated `A–C` edge that skips a level is **kept** and flagged `level_skipping: true`. |

---

## 2. What the code does today

### 2.1 Runtime shape

- **Backend:** Flask 3.0 + Flask-CORS, Python 3.10. Entry point `run.py` → `app.run(debug=True, port=5000)`.
- **Frontend:** Create React App (`react-scripts` 5.0.1), React 18, MUI 5, `vis-network` 9.1 + `vis-data` 7.1, `axios`. Talks to `http://localhost:5000` hardcoded in `frontend/src/services/api.js:4`.
- **No database, no ORM, no migrations, no test suite, no CI.**
- `requirements.txt`: `Flask==3.0.0`, `Flask-CORS==4.0.0`, `biopython==1.83`, `aiohttp==3.9.1`, `requests>=2.31.0`, `python-dotenv>=1.0.0`. `sqlite3` is stdlib, so D9 adds **zero** new dependencies.

### 2.2 Backend file map

| File | Lines | Responsibility today |
|---|---|---|
| `app/__init__.py` | 28 | `create_app()`: imports `app.config` (side-effecting `.env` load), `CORS(app)`, registers blueprint. Module-level `app = create_app()`. |
| `app/config.py` | 43 | Hand-rolled `.env` parser with a `python-dotenv` fast path. Existing env vars win over the file. |
| `app/routes.py` | 436 | All HTTP endpoints + `collect_interactions()` (the whole pipeline lives here). |
| `app/models/protein.py` | 81 | `Protein` dataclass. `node_id` property = `gene_name or uniprot_id`. `search_terms(min_length=4, limit=4)`. |
| `app/models/paper.py` | 34 | `Paper` dataclass: `pmid, title, authors, year, abstract, url, author_list, journal`. No PMCID, no full text, no OA flag. |
| `app/models/interaction.py` | 83 | `Interaction` dataclass. `TYPES` = 4. `normalize_type()`. `key()` **sorts endpoints**. `merge()` unions papers. |
| `app/services/pubmed_service.py` | 364 | `build_query()` (`[tiab]` + MeSH interaction clause), `search_papers()` (esearch, `sort="relevance"`, `retmax=50`), `fetch_paper_details()`, `fetch_multiple_papers()` (batched 100/req), a MEDLINE text parser handling continuation lines and repeatable tags. |
| `app/services/protein_service.py` | 378 | Resolves name/symbol/accession → `Protein` via UniProt. **Hard-scoped to `organism_id:9606 AND reviewed:true`** (line 162). Custom relevance scorer `_score()`. |
| `app/services/protein_resolver.py` | 204 | Symbol canonicalisation via `gene_exact:` queries. **Also hard-scoped to human + reviewed** (line 139). `resolve_many()`, `resolve_accessions()`, `identity_groups()`. |
| `app/services/llm_service.py` | 529 | `OpenAICompatibleBackend` (Groq/OpenAI/Ollama), JSON-mode with 3 escalating retry shapes, 429 handling with provider hint, global pacing lock, `<channel>` unwrapping for reasoning models. `LLMService.EXTRACTION_PROMPT`, `SUMMARY_PROMPT`, `extract_interactions()`, `generate_protein_description()`. |
| `app/services/graph_service.py` | 188 | BFS from the centre over an edge list, `LEVEL_COLORS`/`LEVEL_SIZES` for depth styling, edge id includes the interaction type. |
| `app/utils/cache.py` | 46 | `SimpleCache`: in-memory dict, TTL 3600s, **lost on restart**. |
| `app/utils/rate_limiter.py` | 73 | `AsyncRateLimiter` with a lazily-recreated semaphore per event loop. |
| `app/utils/validation.py` | 126 | `check_graph_protein_uniqueness()` — duplicate node ids, and same-protein-different-name via UniProt accession. |
| `app/utils/logger.py` | — | Console logger. |

### 2.3 Endpoints that exist today

| Method | Path | Behaviour |
|---|---|---|
| POST | `/api/search` | Resolve a name/symbol/accession → `Protein`. 404 with a hint if unresolvable. |
| GET | `/api/protein/<uniprot_id>` | `Protein.to_dict()` plus an LLM summary when configured. Accepts a gene symbol (falls through `ACCESSION_RE`). |
| POST | `/api/interactions` | `collect_interactions()` for one protein. |
| GET | `/api/papers/<pmid>` | One `Paper`. |
| POST | `/api/graph` | `collect_interactions(root)` then `build_graph(root, edges, depth, filters)`. |
| GET | `/api/health` | LLM status + PubMed config flags. |
| DELETE | `/api/cache` | `cache.clear()`. |
| POST | `/api/validate/graph` | Rebuilds the graph, then asserts node uniqueness by UniProt accession. 200 or 409. |

### 2.4 What `collect_interactions()` actually does today (`app/routes.py:51-198`)

1. `protein.search_terms()` → up to 4 terms.
2. Cache key `interactions:{uniprot_id}:{max_papers}:{llm_service.is_configured}`. **No model name, no prompt version.**
3. `pubmed_service.build_query(terms)` → esearch → up to 50 PMIDs (relevance sort).
4. Batch efetch → `Paper` list.
5. For each paper with an abstract, **up to `MAX_ABSTRACTS_FOR_EXTRACTION` (default 20)**, call `llm_service.extract_interactions(abstract, gene_name)`.
6. Build `Interaction` per item, merging duplicates by `key()`.
7. `protein_resolver.resolve_many(endpoints)` → rewrite endpoints to canonical human symbols, drop self-loops, re-merge.
8. Cache the `(interactions, papers, meta)` tuple under the key from step 2.

### 2.5 Frontend file map

| File | Behaviour today |
|---|---|
| `App.js` | MUI theme, `<SearchBar/>` on top, `<FilterControls/>` in a 300px left rail, `<GraphVisualization/>` centre, both drawers mounted. Renders `state.graphData.warning` as an Alert. |
| `context/AppContext.js` | `useReducer` with `currentProtein, graphData, selectedNode, selectedEdge, papers, loading, error, filters (4 booleans), depth`. Two `useState` booleans for drawer visibility. |
| `components/SearchBar.js` | Text field + Search button + a depth `Slider` (1–5) that writes to `state.depth`. On search: `searchProtein()` then `getGraph(uniprot_id, depth, filters)`. |
| `components/GraphVisualization.js` | `vis-network` `Network`, `dedupeById()` guard, `edges: { arrows: 'to' }` for **all** edges, click → edge wins over node → dispatches `SET_SELECTED_EDGE`/`SET_SELECTED_NODE` and opens a drawer. Zoom in/out/reset/export-PNG. |
| `components/ProteinDetailsPanel.js` | Right drawer. Auto-fetches `/api/protein/<id>` on open. "Load Connections" calls `getGraph(selectedNode.id, depth, filters)` and **replaces** `state.graphData` — it re-roots the graph rather than expanding it. |
| `components/PaperListPanel.js` | Left drawer listing `state.papers` (PMIDs), lazily fetches each `/api/papers/<pmid>`, abstract behind a hover tooltip. |
| `components/FilterControls.js` | Four checkboxes bound to `state.filters`. |
| `services/api.js` | Axios instance + request/response logging interceptors. Functions: `searchProtein`, `getProtein`, `getInteractions`, `getPaper`, `getGraph`. |
| `utils/logger.js` | Per-module console loggers. |

---

## 3. Gap analysis, mapped to the four workflow steps

### Step 1 — fetch the 50 papers, using protein name and cross-organism variant names, full text where possible

| ID | Gap | Evidence |
|---|---|---|
| 1.1 | Search terms are **human-entry-only**. `Protein.search_terms()` returns `protein_name`, `gene_name`, and that one entry's `synonyms`. Nothing from other organisms. | `app/models/protein.py:39-66` |
| 1.2 | **No ortholog lookup exists anywhere in the codebase.** Both UniProt clients are hard-scoped `organism_id:9606 AND reviewed:true`, so mouse `Trp53` can never even be looked up. | `app/services/protein_service.py:162`, `app/services/protein_resolver.py:139` |
| 1.3 | Ranking is `sort="relevance"`. PubMed's esearch accepts only `relevance`, `pub_date`, and `Author` — there is no citation sort. Accepted deviation, see §15.1. | `app/services/pubmed_service.py:113` |
| 1.4 | **Abstracts only.** `Entrez.efetch` is called with `db="pubmed"` and `rettype="medline"`. No PMC, no full text. | `app/services/pubmed_service.py:156, 232` |
| 1.5 | **Only 20 of the 50 fetched papers are ever read.** `MAX_ABSTRACTS_FOR_EXTRACTION` defaults to 20, and the loop `continue`s past the rest. So 30 papers are fetched, cached, and discarded. | `app/routes.py:33, 91` |
| 1.6 | The query does not require an interaction verb in the same sentence, so `INTERACTION_TERMS` is doing heavy lifting with a MeSH clause that is not really about PPI. | `app/services/pubmed_service.py:23-27` |

### Step 2 — cache every interaction of the protein of interest, with type and directionality

| ID | Gap | Evidence |
|---|---|---|
| 2.1 | **Directionality is structurally impossible.** `Interaction.key()` does `a, b = sorted([source, target])`, so `A→B` and `B→A` are *the same edge by construction*. The data model cannot express "MDM2 inhibits TP53" distinctly from "TP53 inhibits MDM2". | `app/models/interaction.py:51-57` |
| 2.2 | **Only 4 types.** `activation`, `inhibition`, `phosphorylation`, `ubiquitination` all collapse: `normalize_type` maps unknown strings to `physical_binding`, and `inhibits`/`phosphorylates` are not in the alias table at all. | `app/models/interaction.py:16-45` |
| 2.3 | **The cache is volatile and under-keyed.** In-memory dict, TTL 1h, gone on restart. The key omits the model name and prompt version, so changing `EXTRACTION_PROMPT` silently returns stale extractions. | `app/utils/cache.py:45`, `app/routes.py:74` |
| 2.4 | **No organism on an edge.** `Interaction` has no taxid, so evidence from a mouse paper is indistinguishable from human evidence once ortholog names enter the pipeline. | `app/models/interaction.py:8-13` |
| 2.5 | **There is no per-protein interaction store.** `collect_interactions()` returns a list and throws it away after building one graph. Nothing is queryable by node. | `app/routes.py:192-198` |
| 2.6 | **No evidence record.** `Interaction.papers` is a flat `List[Paper]` and `context` is a single string that `merge()` keeps only if the incumbent's was `None`. The first paper's quote wins arbitrarily; competing quotes are lost. | `app/models/interaction.py:59-67` |

### Step 3 — verify uncovered interactions are distinct, unify duplicates

| ID | Gap | Evidence |
|---|---|---|
| 3.1 | **Cross-species names are never unified — by design.** The human-only scope means mouse `Trp53` and human `TP53` resolve to different symbols and become different nodes. D5 requires the opposite. | `app/services/protein_resolver.py:139` |
| 3.2 | **No ortholog-cluster concept, and nodes carry no taxid.** `Protein` has `organism` (a scientific name string) but no NCBI taxid and no group id. | `app/models/protein.py:22` |
| 3.3 | **Prose names never resolve.** `_SYMBOL_RE = ^[A-Z][A-Z0-9]{1,14}$` rejects anything with a space, and every lookup is a `gene_exact:` query, so `"the tumor suppressor p53"` is never resolvable. | `app/services/protein_resolver.py:34, 138` |
| 3.4 | **No protein-name dictionary at all.** `gene_exact` cannot match a protein name, so the step-1 terms (which per D1/D5 are protein names) have no canonicalisation path. | `app/services/protein_resolver.py:137-155` |
| 3.5 | **Unrecognised symbols become permanent nodes.** `resolve_many` does `resolved.setdefault(symbol, symbol)`, so a hallucinated token survives to the graph. | `app/services/protein_resolver.py:188-189` |
| 3.6 | Distinctness is only *detected* post-hoc by `/api/validate/graph`, never *acted upon*. The validator reports; nothing repairs. | `app/utils/validation.py` |

### Step 4 — repeat steps 1–3 to the user-requested depth

| ID | Gap | Severity |
|---|---|---|
| 4.1 | **The depth control does nothing.** `get_graph` calls `collect_interactions(protein)` **exactly once, for the root**. The resulting edge set contains only edges incident to the root. `build_graph` then runs a BFS over that set, and because no edge joins two first-ring nodes, the BFS terminates after one ring. **`depth` 2, 3, 4 and 5 all return an identical one-ring graph.** The slider in `SearchBar.js:99-106` is cosmetic. | **Critical** |
| 4.2 | No frontier, no per-level budget, no node cap, no wall-clock guard, no cancellation. | High |
| 4.3 | `ProteinDetailsPanel.handleLoadConnections` re-roots the entire graph on the clicked node and discards the current one. The `todo` file asks for "load the new protein's connections" as an *expansion*. | Medium |

### Graph construction rule — no transitive shortcuts

| ID | Finding |
|---|---|
| 5.1 | The rule is **currently satisfied by accident**: `build_graph` only ever emits edges that exist in the input list and never derives one from a path. Nothing to fix — but nothing *enforces* it either. |
| 5.2 | `Interaction.key()`'s endpoint sort actively hides a real `A→B` / `B→A` pair as one edge. Once `directed` exists (D7), this must change or direction is lost at merge time. |
| 5.3 | The iterative crawl (step 4) *will* legitimately produce edges whose endpoints are more than one level apart. These must be kept and flagged, not dropped (D10). |

### Infrastructure issues that block the target design

| ID | Issue | Evidence |
|---|---|---|
| 6.1 | `run_async` is `asyncio.run` per Flask request — a new event loop every time. `AsyncRateLimiter` works around this by recreating its semaphore per loop. A long-lived background job needs one loop, and the pacing lock in `llm_service.py:98` (`self._pace_lock`) is created outside any loop and reused across loops. | `app/routes.py:38-44`, `app/utils/rate_limiter.py:34-44` |
| 6.2 | A capped depth-2 crawl is minutes of work. A synchronous `POST /api/graph` will hit browser and proxy timeouts. D9. |
| 6.3 | `PROTEIN_LLM_ALLOW_PLACEHOLDER_FALLBACK` is set in `.env` but **read nowhere in the code** — dead config left over from the deleted placeholder backend. Remove it. | verified by grep |
| 6.4 | `ProteinDetailsPanel` calls `getProtein(state.selectedNode.id)` where `id` is a bare gene symbol for non-central nodes. `get_protein_by_id` handles it, but every click costs a UniProt round trip. | `frontend/src/components/ProteinDetailsPanel.js:56` |

---

## 4. Target workflow

```
                    ┌─────────────────────────────────────────────┐
USER: protein P ───▶│ 0. RESOLVE  P → Protein + ortholog group    │
   + depth D        │    UniProt: accession, gene, protein name,   │
   + caps           │    taxid, aliases, ortholog cluster          │
                    └────────────────────┬────────────────────────┘
                                         │
       ┌─────────────────────────────────┴─────────────────────────────────┐
       │  FOR level = 0 .. D-1                                            │
       │    frontier = unvisited neighbours of the previous level,         │
       │               ranked by support_count desc, capped at             │
       │               max_nodes_per_level (root exempt)                   │
       └─────────────────────────────────┬─────────────────────────────────┘
                                         ▼
   ┌────────────────────────────────────────────────────────────────────────┐
   │ STEP 1 — PAPERS                                                          │
   │   names  = expand(node)            ← protein names + cross-organism     │
   │                                        variant names, NOT gene symbols  │
   │   pmids  = esearch(build_query(names), retmax=max_papers_per_node)     │
   │            sort=relevance                                              │
   │   papers = efetch(db=pmmed, rettype=medline)                            │
   │   full   = efetch(db=pmc)   for the first fulltext_top_n papers that   │
   │                                have a PMCID                             │
   │   ► all four results written to SQLite before anything else runs        │
   └────────────────────────────────┬───────────────────────────────────────┘
                                    ▼
   ┌────────────────────────────────────────────────────────────────────────┐
   │ STEP 2 — INTERACTIONS, WITH TYPE AND DIRECTION                          │
   │   if node_extractions has a row for (node, cache_signature):           │
   │       edges = load from node_interactions        ← CACHE HIT, 0 LLM    │
   │   else:                                                                │
   │       for each paper: llm.extract(text, node, ortholog_aliases)        │
   │       persist edges + evidence + node_interactions rows                │
   │   each edge carries: interaction_type (4), subtypes[], directed bool,   │
   │                      actor/patient semantics, taxon_a, taxon_b,         │
   │                      ortholog_group_a, ortholog_group_b, evidence[]     │
   └────────────────────────────────┬───────────────────────────────────────┘
                                    ▼
   ┌────────────────────────────────────────────────────────────────────────┐
   │ STEP 3 — DISTINCTNESS AND UNIFICATION                                  │
   │   for every edge endpoint:                                             │
   │     free-text name → (UniProt protein_name | gene | alias | ortholog)   │
   │     raw name       → cluster id                                        │
   │   rewrite both endpoints to their cluster's representative node id      │
   │   drop self-loops and intra-cluster edges                              │
   │   re-key and re-merge; fold conflicting directions/subtypes into flags  │
   │   log: endpoints_rewritten, self_loops_dropped, edges_merged,          │
   │        distinctness_checks_run, unresolved_names                       │
   └────────────────────────────────┬───────────────────────────────────────┘
                                    ▼
   ┌────────────────────────────────────────────────────────────────────────┐
   │ STEP 4 — NEXT LEVEL                                                     │
   │   stop when level+1 == D, or frontier empty, or any cap is hit          │
   │   on any cap hit: record it in truncations[] and surface it to the UI   │
   └────────────────────────────────┬───────────────────────────────────────┘
                                    ▼
   ┌────────────────────────────────────────────────────────────────────────┐
   │ GRAPH BUILD                                                             │
   │   BFS from the root over the FINAL edge set → levels                    │
   │   emit an edge IFF it is in the edge set AND has ≥1 evidence row        │
   │   NEVER synthesise an edge from a path                                  │
   │   flag level_skipping=true where |level(u) − level(v)| > 1              │
   └────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Hard rules that must not be broken

These are the invariants. Each has an automated check in §14.

1. **No derived edges.** An edge exists in the output **iff** at least one evidence row supports it. Never emit `A–C` because a path `A–B–C` exists.
2. **The extraction anchor stays.** The prompt requires one side of every extracted pair to be the node being crawled (or one of its ortholog aliases). This is deliberate: it is the mechanism that enforces rule 1 and bounds the crawl. A pair of two *other* proteins mentioned in the same paper is not extracted at that node — it is extracted when one of those proteins is itself crawled. **Do not "fix" this by removing the anchor rule;** that is precisely how spurious transitive edges would enter the graph.
3. **No fabrication.** `llm_service.py` deliberately has no placeholder backend. Keep it that way. An empty graph with an explanatory warning is correct; a populated graph of invented edges is not.
4. **Real annotations outrank generated text.** UniProt `cc_function` / `cc_pathway` / `cc_subcellular_location` are authoritative. LLM summaries are additive only and never overwrite.
5. **Every cap that truncates must be reported.** If the crawl stopped early because of `max_llm_calls`, the response says so, with the count.
6. **Human-only is no longer assumed, but human is still the reference.** Nodes are labelled with the human reference gene symbol where one exists; evidence keeps its own taxid. Cross-species evidence must never be silently relabelled as human.

---

## 6. Data model changes

### 6.1 `app/models/interaction.py` — rewrite

```python
@dataclass
class Interaction:
    source_protein: str                  # actor when directed; arbitrary-but-stable when not
    target_protein: str
    interaction_type: str                # one of the 4 display types
    directed: bool = False               # D7: source is the ACTOR when True
    subtypes: List[str] = field(default_factory=list)   # D6, may be empty
    taxon_a: Optional[int] = None        # NCBI taxid of source_protein's species
    taxon_b: Optional[int] = None        # NCBI taxid of target_protein's species
    group_a: Optional[str] = None        # ortholog cluster of source_protein
    group_b: Optional[str] = None        # ortholog cluster of target_protein
    papers: List[Paper] = field(default_factory=list)   # kept for back-compat, derived
    evidence: List["Evidence"] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)  # e.g. "direction", "subtype:activation|inhibition"
```

**The four display types stay exactly as they are** (D6):

```python
TYPES = {
    "physical_binding": "Physical Binding",
    "regulatory":       "Regulatory",
    "complex_formation":"Complex Formation",
    "genetic":          "Genetic",
}
```

**The subtype vocabulary (D6)** — stored in `subtypes`, filtered in the UI, never a graph edge type:

```python
SUBTYPES = {
    "activation":                "Activation",
    "inhibition":                "Inhibition",
    "phosphorylation":           "Phosphorylation",
    "dephosphorylation":         "Dephosphorylation",
    "ubiquitination":            "Ubiquitination",
    "deubiquitination":          "Deubiquitination",
    "sumoylation":               "SUMOylation",
    "acetylation":               "Acetylation",
    "methylation":               "Methylation",
    "glycosylation":             "Glycosylation",
    "cleavage":                  "Cleavage",
    "transcriptional_regulation":"Transcriptional Regulation",
    "post_translational_regulation": "Post-translational Regulation",
    "binding":                   "Binding",
}
# Precedence used to pick `primary_subtype` when observations disagree. Most
# mechanistic claim wins over the generic one.
SUBTYPE_PRECEDENCE = [
    "inhibition", "activation", "phosphorylation", "dephosphorylation",
    "ubiquitination", "deubiquitination", "sumoylation", "acetylation",
    "methylation", "glycosylation", "cleavage", "transcriptional_regulation",
    "post_translational_regulation", "binding",
]
```

**`normalize_type` becomes two functions.** `normalize_type` keeps its current behaviour and signature for the 4 display types. Add:

```python
@staticmethod
def normalize_subtype(value) -> Optional[str]:
    """Map free text onto SUBTYPES, or None when nothing specific was stated.

    Deliberately permissive about the verb ("phosphorylates", "is
    phosphorylated by", "ubiquitylated") and strict about the output.
    Never returns a value outside SUBTYPES.
    """
```

Alias rules to implement: strip, lower-case, `-`/space → `_`, then map
`phosphorylates|phosphorylated|phosphorylation` → `phosphorylation`;
`ubiquitylates|ubiquitylated|ubiquitinates|ubiquitination|ub` → `ubiquitination`;
`sumoylated|sumoylation|sUMO` → `sumoylation`;
`acetylated|acetylation` → `acetylation`; `methylated|methylation` → `methylation`;
`glycosylated|glycosylation` → `glycosylation`; `cleaved|cleavage|cuts` → `cleavage`;
`dephosphorylates|dephosphorylation` → `dephosphorylation`;
`deubiquitinates|deubiquitination` → `deubiquitination`;
`activates|activation|induces|upregulates|stimulates` → `activation`;
`inhibits|inhibition|represses|downregulates|blocks|suppresses` → `inhibition`;
`transcriptionally_regulates|binds_transcription_factor` → `transcriptional_regulation`.
Anything else → `None`.

**New `key()`.** This is the D7/D10 crux:

```python
def key(self) -> tuple:
    """Identity of an edge, independent of which paper reported it.

    Directed edges are keyed in actor→patient order, so "MDM2 inhibits TP53"
    and "TP53 inhibits MDM2" are two DIFFERENT edges. Undirected edges sort
    their endpoints, so a physical binding is stored once.

    `subtypes` is deliberately NOT part of the key. If it were, a paper
    reporting subtype=None and another reporting subtype=activation would
    produce two edges for one relationship, and the merge would be
    order-dependent. Subtypes accumulate on the single edge instead
    (see merge()) and disagreements are recorded in `conflicts`.
    """
    if self.directed:
        return (self.source_protein, self.target_protein, self.interaction_type)
    a, b = sorted([self.source_protein, self.target_protein])
    return (a, b, self.interaction_type)
```

**New `merge()`.** Must be order-independent:

```python
def merge(self, other: "Interaction") -> None:
    """Fold another observation of the same edge into this one.

    - papers:    union by pmid
    - evidence:  union by (pmid, source)
    - subtypes:  union, preserving SUBTYPE_PRECEDENCE order
    - directed:  True if EITHER observation is directed; if they disagree,
                 record "direction" in conflicts
    - taxons:    keep self's if set, else adopt other's (never overwrite a
                 known taxon with None); if both set and different, record
                 "taxon_a" / "taxon_b" in conflicts
    - context:   keep self's if set, else adopt other's (first non-empty wins,
                 which is arbitrary but stable — the full evidence list is the
                 real record)
    """
```

**New `primary_subtype` property:** the first entry of `self.subtypes` ordered by `SUBTYPE_PRECEDENCE`, or `None`.

**New `to_dict()`** must additionally emit `directed`, `subtypes`, `primary_subtype`, `taxon_a`, `taxon_b`, `group_a`, `group_b`, `conflicts`, and `evidence` (each with `pmid, source, context, taxon_a, taxon_b`).

### 6.2 `app/models/evidence.py` — new file

```python
@dataclass
class Evidence:
    """One paper's support for one edge. The unit of provenance."""
    pmid: str
    source: str                # "llm_abstract" | "llm_fulltext" | "llm_summary"
    context: Optional[str]     # verbatim quote justifying the pair
    taxon_a: Optional[int] = None
    taxon_b: Optional[int] = None
    ortholog_alias_used: Optional[str] = None   # which alias matched, e.g. "Trp53"
    extracted_at: Optional[str] = None          # ISO-8601 UTC
```

`Evidence` is what makes rule 1 mechanically checkable: an edge without an `Evidence` row is not a real edge.

### 6.3 `app/models/paper.py` — extend

Add fields (all defaulted, so existing constructors keep working):

```python
pmcid: Optional[str] = None
is_open_access: Optional[bool] = None
fulltext: Optional[str] = None          # plain text, sections concatenated
fulltext_source: Optional[str] = None   # "pmc_xml"
fulltext_fetched_at: Optional[str] = None
```

### 6.4 `app/models/protein.py` — extend

```python
taxid: Optional[int] = None
ortholog_group: Optional[str] = None
protein_name_variants: List[str] = field(default_factory=list)  # one per ortholog entry
```

Replace `search_terms()` with a name-first implementation. Per the workflow's
"(not associated gene names)", the PubMed query must be built from **protein
names and aliases, not gene symbols**:

```python
def search_terms(self, min_length: int = 4, limit: int = 6,
                 include_gene_symbols: bool = False) -> List[str]:
    """Phrases worth searching PubMed for, most specific first.

    Protein names come first and gene symbols last, and gene symbols are
    excluded entirely unless include_gene_symbols is set. This is deliberate:
    "CAT"[tiab] returns 4,972 hits versus 1,335 for "Catalase"[tiab], and
    "INS"/"AR" are ordinary English words. The 4-character floor is kept as a
    second line of defence.
    """
```

Candidate order: `protein_name`, then `protein_name_variants` (deduped,
case-insensitively), then `synonyms`, then — only if `include_gene_symbols` —
`gene_name`.

---

## 7. SQLite schema

New file `app/services/db.py`. Use stdlib `sqlite3`. Enable
`PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON`. All writes go through
`INSERT ... ON CONFLICT DO UPDATE`. `schema_meta.version` drives migrations; bump
it and add an idempotent migration function per version.

```sql
CREATE TABLE IF NOT EXISTS schema_meta (
  version INTEGER NOT NULL
);

-- ---------- proteins and identity ----------

CREATE TABLE IF NOT EXISTS ortholog_groups (
  group_id      TEXT PRIMARY KEY,     -- "GRP:<sha1(representative_accession)[:12]>"
  accession     TEXT NOT NULL,        -- representative (human when available)
  node_id       TEXT NOT NULL,        -- representative node id
  gene_name     TEXT,
  protein_name  TEXT,
  taxid         INTEGER,
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ortholog_members (
  group_id   TEXT NOT NULL REFERENCES ortholog_groups(group_id) ON DELETE CASCADE,
  accession  TEXT NOT NULL,
  node_id    TEXT NOT NULL,
  gene_name  TEXT,
  protein_name TEXT,
  taxid      INTEGER,
  organism   TEXT,
  is_reference INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (group_id, accession)
);

-- alias_norm is the de-duplication key. Same alias_norm => same protein.
CREATE TABLE IF NOT EXISTS protein_aliases (
  alias_norm TEXT NOT NULL,
  node_id    TEXT NOT NULL,
  alias_raw  TEXT NOT NULL,
  kind       TEXT NOT NULL,     -- gene_symbol|protein_name|synonym|locus_name|entry_name|free_text
  taxid      INTEGER,
  source     TEXT NOT NULL,     -- uniprot_search|uniprot_entry|llm|manual
  PRIMARY KEY (alias_norm, node_id)
);
CREATE INDEX IF NOT EXISTS ix_alias_norm ON protein_aliases(alias_norm);

CREATE TABLE IF NOT EXISTS name_search_index (
  -- the protein-name strings fed to esearch, one row per term
  term_norm   TEXT PRIMARY KEY,
  term_raw    TEXT NOT NULL,
  group_id    TEXT
);

-- ---------- literature ----------

CREATE TABLE IF NOT EXISTS papers (
  pmid            TEXT PRIMARY KEY,
  title           TEXT,
  authors         TEXT,
  year            TEXT,
  journal         TEXT,
  abstract        TEXT,
  url             TEXT,
  pmcid           TEXT,
  is_open_access  INTEGER,
  fulltext        TEXT,
  fulltext_source TEXT,
  fulltext_fetched_at TEXT,
  fetched_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_queries (
  -- which search surfaced this paper, and at what rank
  query_hash TEXT NOT NULL,     -- sha1 of the exact query string
  pmid       TEXT NOT NULL,
  term_norm  TEXT NOT NULL,     -- the protein term that matched
  node_id    TEXT NOT NULL,
  rank       INTEGER NOT NULL,
  PRIMARY KEY (query_hash, pmid)
);

-- ---------- interactions ----------

CREATE TABLE IF NOT EXISTS interactions (
  edge_id            TEXT PRIMARY KEY,   -- "|".join(interaction.key())
  source_node        TEXT NOT NULL,      -- actor when directed
  target_node        TEXT NOT NULL,
  interaction_type   TEXT NOT NULL,      -- one of the 4
  directed           INTEGER NOT NULL DEFAULT 0,
  subtypes_json      TEXT NOT NULL DEFAULT '[]',
  conflicts_json     TEXT NOT NULL DEFAULT '[]',
  taxon_a            INTEGER,
  taxon_b            INTEGER,
  group_a            TEXT,
  group_b            TEXT,
  support_count      INTEGER NOT NULL DEFAULT 0,
  first_seen         TEXT,
  last_seen          TEXT
);
CREATE INDEX IF NOT EXISTS ix_int_src ON interactions(source_node);
CREATE INDEX IF NOT EXISTS ix_int_tgt ON interactions(target_node);

CREATE TABLE IF NOT EXISTS interaction_evidence (
  edge_id   TEXT NOT NULL REFERENCES interactions(edge_id) ON DELETE CASCADE,
  pmid      TEXT NOT NULL,
  source    TEXT NOT NULL,        -- llm_abstract|llm_fulltext|llm_summary
  context   TEXT,
  taxon_a   INTEGER,
  taxon_b   INTEGER,
  ortholog_alias_used TEXT,
  extracted_at TEXT,
  PRIMARY KEY (edge_id, pmid, source)
);
CREATE INDEX IF NOT EXISTS ix_ev_edge ON interaction_evidence(edge_id);

-- Step 2's queryable per-protein store. One row per (node, edge).
CREATE TABLE IF NOT EXISTS node_interactions (
  node_id   TEXT NOT NULL,
  edge_id   TEXT NOT NULL REFERENCES interactions(edge_id) ON DELETE CASCADE,
  role      TEXT NOT NULL,      -- 'source' | 'target'
  taxid     INTEGER,
  first_seen TEXT,
  PRIMARY KEY (node_id, edge_id)
);
CREATE INDEX IF NOT EXISTS ix_ni_node ON node_interactions(node_id);

-- Lets the crawler skip the LLM entirely on a repeat run.
CREATE TABLE IF NOT EXISTS node_extractions (
  node_id      TEXT NOT NULL,
  signature    TEXT NOT NULL,     -- see §9.2 cache_signature()
  status       TEXT NOT NULL,     -- ok | empty | error
  papers_seen  INTEGER NOT NULL DEFAULT 0,
  llm_calls    INTEGER NOT NULL DEFAULT 0,
  edges_found  INTEGER NOT NULL DEFAULT 0,
  error        TEXT,
  extracted_at TEXT NOT NULL,
  PRIMARY KEY (node_id, signature)
);

-- ---------- jobs ----------

CREATE TABLE IF NOT EXISTS jobs (
  job_id       TEXT PRIMARY KEY,
  status       TEXT NOT NULL,     -- queued|running|done|error|cancelled|interrupted
  root_node_id TEXT NOT NULL,
  depth        INTEGER NOT NULL,
  params_json  TEXT NOT NULL,
  progress_json TEXT NOT NULL DEFAULT '{}',
  result_json  TEXT,
  error        TEXT,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);
```

**Durability contract.** `app/utils/cache.py::SimpleCache` stays as the
in-process L1 for hot objects (unchanged interface, so no existing caller
breaks), but every read that must survive a restart goes to SQLite. On import,
`db.mark_interrupted_jobs()` flips any `running` job to `interrupted`.

---

## 8. New modules

### 8.1 `app/services/db.py`

Thin, explicit data-access layer. **No ORM.** Every method is idempotent.

```python
class Database:
    def __init__(self, path: str = None): ...          # env PROTEIN_DB_PATH, default data/protein_friend_finder.db
    def connect(self) -> sqlite3.Connection: ...       # per-thread, check_same_thread=False + Lock
    def migrate(self) -> None: ...
    def mark_interrupted_jobs(self) -> int: ...

    # literature
    def upsert_paper(self, paper: Paper) -> None: ...
    def get_paper(self, pmid: str) -> Optional[Paper]: ...
    def record_paper_query(self, query_hash, pmid, term_norm, node_id, rank) -> None: ...
    def get_query_hash(self, query: str) -> str: ...

    # identity
    def upsert_ortholog_group(self, group) -> None: ...
    def upsert_ortholog_member(self, group_id, accession, node_id, gene_name,
                               protein_name, taxid, organism, is_reference) -> None: ...
    def upsert_alias(self, alias_norm, node_id, alias_raw, kind, taxid, source) -> None: ...
    def resolve_alias(self, alias_norm) -> Optional[str]: ...     # -> node_id
    def resolve_aliases(self, alias_norms: List[str]) -> Dict[str, Optional[str]]: ...
    def get_group(self, group_id) -> Optional[dict]: ...
    def members_of(self, group_id) -> List[dict]: ...

    # interactions
    def save_interactions(self, node_id, interactions: List[Interaction]) -> None: ...
    def get_node_interactions(self, node_id) -> List[Interaction]: ...
    def count_node_interactions(self, node_id) -> int: ...
    def edge_has_evidence(self, edge_id) -> bool: ...
    def has_extraction(self, node_id, signature) -> Optional[dict]: ...
    def record_extraction(self, node_id, signature, status, papers_seen,
                          llm_calls, edges_found, error=None) -> None: ...
    def all_interactions(self) -> List[Interaction]: ...

    # jobs
    def create_job(self, job_id, root_node_id, depth, params) -> None: ...
    def update_job(self, job_id, **fields) -> None: ...
    def get_job(self, job_id) -> Optional[dict]: ...
```

`save_interactions` must, in one transaction: upsert the `interactions` row,
insert each `interaction_evidence` row (`INSERT OR IGNORE` on the PK so
re-extraction is free), upsert both `node_interactions` rows, and recompute
`support_count = COUNT(*)` from `interaction_evidence`.

### 8.2 `app/services/name_expansion.py` — step 1's cross-organism names + step 3's identity

```python
@dataclass
class NameSet:
    node_id: str
    group_id: str
    reference_accession: str          # human when available
    protein_names: List[str]          # the human protein name + ortholog variants
    aliases: List[str]                # every synonym / locus name seen
    gene_symbols_by_taxon: Dict[int, str]
    members: List[dict]               # one per ortholog accession
    search_terms: List[str]           # ready for PubMedService.build_query
```

```python
class NameExpansionService:
    def __init__(self, database, uniprot_client, timeout: int = 30): ...

    async def expand(self, protein: Protein) -> NameSet:
        """Resolve a protein into its ortholog cluster and search names.

        Implementation, verified against the live API:

        1. If a name_search_index / ortholog_members row already covers this
           accession, return it. (Every later call is a cache hit.)
        2. Primary query, ONE request, no organism filter:
             (protein_name:"<human protein name>") AND (reviewed:true)
           fields=accession,id,gene_names,protein_name,organism_name,organism_id
           size=500
           Verified: for "Cellular tumor antigen p53" this returns P10361 Tp53
           (rat), P02340 Trp53 (mouse), P79734 tp53 (zebrafish), P04637 TP53
           (human), and ~10 more.
        3. Fallback when the protein has no usable protein_name, or to widen
           coverage — a SECOND query with no organism filter:
             (gene_exact:<SYMBOL>) AND (reviewed:true)
           Symbols are used only to FIND ortholog entries; they are never
           emitted into search_terms (the workflow excludes gene names).
        4. Keep at most max_organisms entries, ordered by
           PROTEIN_ORTHOLOG_SPECIES_PRIORITY so the query string stays bounded.
        5. group_id = "GRP:" + sha1(reference_accession)[:12]; persist the
           group, its members, and every alias (kind=gene_symbol|protein_name|
           synonym|locus_name|entry_name) into protein_aliases.
        6. search_terms = protein_name + ortholog protein_name_variants +
           aliases, deduped case-insensitively, min length 4, gene symbols
           excluded. Capped at PROTEIN_SEARCH_MAX_TERMS.
        """
```

**Robustness requirements.** Chunk the UniProt query at 40 clauses (URL length,
mirroring `protein_resolver.py:162`). Never raise: on total failure return a
`NameSet` containing only the human entry's own names, so the crawl still
proceeds with degraded recall rather than erroring.

### 8.3 `app/services/ortholog_service.py` — step 3, identity resolution

```python
class OrthologService:
    def __init__(self, database, name_expansion, llm_service=None, timeout: int = 30): ...

    def alias_key(self, name: str) -> str:
        """Normalised de-duplication key for a free-text protein mention.

        Lower-case, strip, collapse whitespace and punctuation. This is the
        key that catches "p53" / "P53" / "p-53" / "TP53*" without a network
        call. Orthology itself is decided by accession, not by this key.
        """

    async def resolve_name(self, name: str, anchor: NameSet) -> Resolution:
        """Resolve one free-text name to a node, in this order, cheapest first:

        1. database.resolve_alias(alias_key(name))                 -- free
        2. exact match against anchor.members' gene symbols,
           protein names and aliases                          -- free
        3. UniProt (protein_name | gene_exact) search, NO organism
           filter, so orthologs are reachable at all           -- 1 request
        4. one LLM disambiguation call, only if the name is not a
           bare symbol and steps 1-3 failed                    -- 1 LLM call
        5. give up: return Resolution(node_id=None, ...) and let
           the caller keep the raw string as a node id, recording it
           in unresolved_names.

        Never returns a fabricated accession. Step 5 is the important one:
        a name we cannot identify is still a real mention in a real paper,
        so the node is kept, but it is flagged unresolved and the flag is
        surfaced in the response.
        """

    async def resolve_names(self, names: List[str], anchor: NameSet) -> Dict[str, Resolution]: ...

    def unify(self, interactions: List[Interaction],
              mapping: Dict[str, Resolution]) -> UnifyReport:
        """Step 3's rewrite pass. Returns the rewritten edges plus a report.

        - rewrite both endpoints to their node_id
        - drop self-loops (source == target after rewriting)
        - drop intra-cluster edges (group_a == group_b) — these arise when a
          paper describes the same protein under two of its own aliases
        - re-key directed edges so the actor stays the source; a directed edge
          whose endpoints swapped during resolution MUST be flipped back, and
          the flip recorded
        - re-merge collisions, unioning subtypes and recording conflicts
        - re-check direction after canonicalisation: if the merge made one
          edge both directed and undirected, set directed=True and push
          "direction" into conflicts
        """
```

`UnifyReport` fields, all surfaced in the API response:
`endpoints_rewritten`, `endpoints_unchanged`, `self_loops_dropped`,
`intra_cluster_dropped`, `edges_merged`, `direction_flips`,
`distinctness_checks_run`, `unresolved_names`, `clusters_touched`.

### 8.4 `app/services/llm_extraction` — folded into `llm_service.py`

No new module. See §9.4 for the exact changes.

### 8.5 `app/services/crawl_service.py` — steps 1–4, the engine

```python
@dataclass
class CrawlParams:
    root_node_id: str
    depth: int = 2
    max_depth: int = 3
    max_papers_per_node: int = 50
    max_nodes_per_level: int = 10
    max_llm_calls: int = 200
    max_wall_clock_s: float = 180.0
    fulltext_top_n: int = 10
    use_fulltext: bool = True
    filters: Optional[List[str]] = None
    # expansion: crawl the existing graph around a clicked node instead of
    # re-rooting (this is what `todo` line 14 asks for)
    merge_into: Optional[dict] = None     # {"levels": {...}, "visited": [...], "edges": [...]}

    @classmethod
    def from_env(cls, **overrides) -> "CrawlParams": ...
    def to_dict(self) -> dict: ...
```

```python
class CrawlService:
    def __init__(self, database, pubmed_service, llm_service, name_expansion,
                 ortholog_service, graph_service): ...

    async def run(self, params: CrawlParams, on_progress=None,
                  should_cancel=None) -> CrawlResult:
        """BFS crawl. `on_progress(dict)` is awaited after every node.
        `should_cancel()` is polled between nodes.
        """

    async def _crawl_node(self, node, level, params, budget) -> Tuple[List[Interaction], dict]:
        """Steps 1→3 for a single node. Returns (edges, per-node stats)."""

    def _select_frontier(self, edges, level, visited, params) -> List[str]:
        """Rank unvisited neighbours by support_count desc, then alphabetically
        for determinism, and cap at max_nodes_per_level. The root is exempt.
        """
```

**Algorithm, precisely:**

```
budget = Budget(max_llm_calls, max_wall_clock_s, llm_calls=0, started=monotonic())
visited = {root}; levels = {root: 0}
if params.merge_into:  seed visited/levels/edges from the parent graph
edges: Dict[edge_id, Interaction] = {}
frontier = [root]
truncations: List[dict] = []

for level in 0 .. params.depth-1:
    if frontier is empty: break
    if budget.exhausted(): truncations.append(reason); break
    if should_cancel(): raise Cancelled

    # ---- STEP 1
    for node in frontier:
        names  = await name_expansion.expand(node)          # L1 + SQLite
        query  = pubmed_service.build_query(names.search_terms)
        pmids  = await pubmed_service.search_papers(query, max_results=max_papers_per_node)
        papers = await pubmed_service.fetch_multiple_papers(pmids)
        if params.use_fulltext:
            for paper in papers[:fulltext_top_n]:
                if not paper.pmcid: continue
                await pubmed_service.fetch_fulltext(paper)     # writes to SQLite
        database.record_paper_query(...)                      # for all pmids, with rank

    # ---- STEP 2
    for node in frontier:
        signature = cache_signature(node, params)
        if database.has_extraction(node, signature):
            node_edges = database.get_node_interactions(node)   # 0 LLM calls
            stats.extraction_cache_hits += 1
        else:
            if budget.llm_calls >= budget.max_llm_calls:
                truncations.append({"reason": "max_llm_calls", ...}); break
            node_edges, used = await self._extract(node, names, papers, params, budget)
            database.record_extraction(node, signature, ...)
        merge node_edges into `edges` (union subtypes, union evidence, record conflicts)

    # ---- STEP 3
    mapping = await ortholog_service.resolve_names(all endpoints, root_names)
    edges, report = ortholog_service.unify(list(edges.values()), mapping)
    edges = {e.key(): e for e in edges}

    # ---- STEP 4
    if level + 1 >= params.depth: break
    visited |= set(edges)  keys' endpoints
    next_frontier = _select_frontier(edges, level, visited, params)
    if len(candidates) > max_nodes_per_level:
        truncations.append({"reason": "max_nodes_per_level", "level": level+1,
                            "candidates": n, "kept": k, "dropped": [ids]})
    frontier = next_frontier
```

`CrawlResult` carries: `edges`, `levels`, `papers`, `truncations`,
`unify_report`, `per_node_stats`, `budget_used`, `params`, `warnings`.

**`cache_signature(node, params)`** — the fix for gap 2.3. It is a sha1 over:

```
f"{node.node_id}|papers={max_papers_per_node}|fulltext={use_fulltext}"
f"|ft_top_n={fulltext_top_n}|prompt={PROTEIN_PROMPT_VERSION}"
f"|model={llm_service.backend.model}|anchored={1}"
```

Changing `EXTRACTION_PROMPT`, the model, or any crawl parameter therefore
invalidates exactly the affected cache entries and nothing else.

**Per-node LLM budget arithmetic.** `llm_calls_per_node` =
`len(abstracts_considered) + fulltext_chunks` for that node. Before starting a
node, check `budget.llm_calls + estimate <= budget.max_llm_calls`; if not, skip
the node and record `{"reason": "max_llm_calls", "node": node_id}`. Never start
work that cannot finish.

### 8.6 `app/services/job_service.py` — D9

```python
class JobService:
    def __init__(self, max_concurrent: int = 1): ...
    def start(self) -> None:          # boots the single worker thread + its event loop
    def submit(self, kind: str, coro_factory) -> str:   # -> job_id
    def get(self, job_id) -> dict
    def cancel(self, job_id) -> bool
    def shutdown(self) -> None
```

Implementation requirements, all forced by gap 6.1:

1. **One `threading.Thread` running one `asyncio.new_event_loop()` with
   `run_forever()`.** All crawl coroutines are scheduled onto it with
   `asyncio.run_coroutine_threadsafe`. Never `asyncio.run` per job — that
   rebinds `AsyncRateLimiter`'s semaphore and `llm_service._pace_lock` to a new
   loop each time, which is the bug the current `run_async` is working around.
2. The worker loop is started once, lazily, on the first `submit`, and lives
   for the process lifetime.
3. Job state is a `threading.Lock`-guarded dict, mirrored into `db.jobs` on
   every transition. `progress_json` is written at most once per second to
   avoid hammering SQLite.
4. `max_concurrent` defaults to 1, because a single Groq key is already the
   bottleneck; a second concurrent crawl would only produce 429s.
5. On boot, `db.mark_interrupted_jobs()`.
6. `progress` dict shape:
   ```json
   {"phase": "crawling", "level": 1, "nodes_done": 3, "nodes_total": 10,
    "papers_fetched": 210, "llm_calls": 47, "edges": 63, "fulltext_fetched": 8,
    "cache_hits": 2, "elapsed_s": 41.2, "budget": {"llm": 47, "max_llm": 200,
    "max_nodes_per_level": 10, "max_depth": 3, "max_wall_clock_s": 180},
    "current_node": "MDM2", "message": "Extracting from 12 papers"}
   ```

---

## 9. Modified modules — exact changes

### 9.1 `app/services/pubmed_service.py`

**a) `build_query` must accept a `NameSet`.** Add an overload; keep the existing
`build_query(terms: Sequence[str])` signature working, because
`app/routes.py:82` calls it and the validation endpoint does too.

```python
@staticmethod
def build_query(terms, interaction_terms: str = None, name_set=None) -> Optional[str]
```

When `name_set` is given, the subject clause is built from
`name_set.search_terms`, which per D5 contains **protein names and aliases, not
gene symbols**. Quote each term and tag `[Title/Abstract]`. Keep the existing
parenthesisation fix (every term parenthesised, so `AND` binds tighter than the
`OR`s) — that bug is already fixed and must not regress.

**b) `search_papers` — keep `sort="relevance"`** (D1). Add a `query_hash` return
path so `paper_queries` rows can be written, and log that ranking is relevance,
not citation, so nobody later "discovers" this as a bug.

**c) New: PMCID + OA flag.** `Entrez.esummary` is unnecessary — the batch
`efetch(rettype="medline")` response already carries a `PMC` line. Verified on
`efetch(db="pubmed", id="2943983,25603305", rettype="medline", retmode="text")`:

```
IS  - 0270-7306 (Print)
LID - S2210-2612(14)00401-5 [pii]
PMC - PMC369122          <-- this line
PMCR- 1985/11/01
AID - 10.1128/mcb.5.11.3084-3091-1985 [doi]
```

Implementation: read `values.get("PMC")`, strip it, and store
`Paper.pmcid = "PMC369122"`.

Two traps, both verified:

- `PMC` and `PMCR` are different tags. `line[:4]` for the second is `"PMCR"`, so
  the existing parser stores it separately and there is no collision. Do **not**
  add `PMCR` to `REPEATABLE_TAGS` and do not read `PMC` from `LID`/`AID` — the
  `LID` and `AID` lines hold the `[pii]` and `[doi]` values, not the PMCID.
- `PMC` is not currently in `REPEATABLE_TAGS`
  (`app/services/pubmed_service.py:30`), so `_parse_medline` stores it as a
  single value. That is correct — a MEDLINE record has at most one `PMC` line.
  Leave it out of `REPEATABLE_TAGS`; adding it would concatenate duplicates.

Set `Paper.is_open_access = bool(pmcid)`. Treat that as "a PMCID exists", not as
a licence assertion — `isOpenAccess` in the Europe PMC sense is stricter, and
we are not calling Europe PMC (D3).

**d) New: `fetch_full_text(paper) -> Optional[str]`.**

```python
async def fetch_full_text(self, paper: Paper) -> Optional[str]:
    """Fetch PMC full text and flatten it to plain text.

    Verified: Entrez.efetch(db="pmc", id="PMC3148502", rettype="xml",
    retmode="xml") returns 121,767 bytes of JATS containing <body>.

    Implementation:
      - no PMCID -> return None (not an error; ~70% of papers)
      - cache key f"fulltext:{pmcid}"; also consult db.get_paper(pmid)
      - parse with xml.etree.ElementTree, walking //body//p and //body//sec/title
      - DROP in this order: <xref> (citation clutter), <table-wrap>, <fig>,
        <ref-list>, <front> (metadata), <abstract> (already used separately),
        <supplementary-material>
      - keep <title> of each <sec> as a line prefix so the LLM knows the section
      - collapse whitespace, then truncate to PROTEIN_FULLTEXT_MAX_CHARS
      - on any exception return None and log; never propagate. A missing full
        text degrades recall, it must not fail the crawl.
    """
```

**e) New: `chunk_for_llm(text, chunk_chars) -> List[str]`.** Split on
paragraph boundaries. Sections named `Results`, `Methods`, `Discussion` and
`Interactions`/`Interactors` are prioritised; a `Results` chunk yields far more
extractions than an `Introduction` chunk, and this ordering is free.

### 9.2 `app/utils/cache.py`

Keep `SimpleCache` exactly as-is for L1. Add a module-level L2 that is a thin
pass-through to `Database` for the three durable keyspaces
(`paper:`, `fulltext:`, `protein:`), so existing `cache.get`/`cache.set` call
sites start surviving restarts without being rewritten. `SimpleCache.stats()`
gains a `durable_rows` field from `db.stats()`.

### 9.3 `app/services/protein_service.py` and `app/services/protein_resolver.py`

Both are hard-scoped to `organism_id:9606 AND reviewed:true` (lines 162 and
139). That scope is correct for *resolving the user's query to a human protein*
and must stay. It is wrong for *resolving a partner name found in a paper*,
which may be mouse, rat or yeast.

- `ProteinService`: unchanged. It is the entry-point resolver.
- `ProteinResolver`: add an `organism` override per call.
  `async def resolve_many(self, symbols, organism: str = None)` where `None`
  means "no organism filter at all". `OrthologService` calls it with
  `organism=None`. Add a `by_protein_name(name)` method using
  `protein_name:"<name>"` with no organism filter, which is the query that
  actually finds orthologs (verified in §8.2 step 2).
- Add `ProteinResolver.looks_like_symbol` usage: a bare symbol goes straight to
  `gene_exact`; anything with a space or lowercase prose goes to
  `protein_name`/`cc_similarity` first. This closes gap 3.3.

### 9.4 `app/services/llm_service.py`

**a) Add `PROTEIN_PROMPT_VERSION`** (default `"v2"`), exported and included in
`cache_signature()`.

**b) Replace `EXTRACTION_PROMPT` with `EXTRACTION_PROMPT_V2`.** Keep the old
constant for one release so old cache rows stay explainable.

```python
EXTRACTION_PROMPT_V2 = """You are a biomedical information extraction system.

Task: read the {source_label} below and list ONLY the protein-protein
interactions it actually states or clearly implies.

Strict rules:
- The protein of interest is {protein}. At least one side of every pair must be
  {protein} or one of its names in other organisms: {ortholog_aliases}.
- Use official gene symbols in CAPS, written the way the source organism writes
  them (human TP53, mouse Trp53, rat Tp53, zebrafish tp53). If the organism is
  not stated, use the symbol for {anchor_organism}.
- Report a pair only if a physical or regulatory relationship between the two is
  stated. Co-occurrence in one sentence is NOT an interaction. Stated
  co-membership in a named complex IS an interaction.
- interaction_type must be exactly one of: physical_binding, regulatory,
  complex_formation, genetic.
- subtype must be exactly one of: activation, inhibition, phosphorylation,
  dephosphorylation, ubiquitination, deubiquitination, sumoylation,
  acetylation, methylation, glycosylation, cleavage,
  transcriptional_regulation, post_translational_regulation, or null when the
  text states no more specific mechanism.
- directed must be true when the text says which protein acts on which
  (A phosphorylates B, A activates B, A is cleaved by B, A represses B's
  transcription). directed must be false for physical_binding,
  complex_formation and genetic.
- When directed is true, source_protein is ALWAYS the acting protein and
  target_protein is ALWAYS the protein acted upon.
- organism_a and organism_b must be the NCBI taxon id of each protein's
  species: 9606 human, 10090 mouse, 10116 rat, 7955 zebrafish, 7227 fruit fly,
  6239 roundworm, 4932 yeast, 3702 arabidopsis. Use {anchor_taxid} if unstated.
- Ignore section headers, database names, cell lines, tissue names, disease
  abbreviations and dataset accessions. They are not proteins.
- context must be a short verbatim quote that justifies the pair and contains
  the verb that states the relationship.
- If the text states no interaction involving {protein}, return an empty list.
  An empty list is a correct and useful answer.

{body}

Respond with JSON only, no commentary:
{{"interactions": [{{"source_protein": "EGFR", "target_protein": "GRB2",
  "interaction_type": "physical_binding", "subtype": null, "directed": false,
  "organism_a": 9606, "organism_b": 9606, "context": "short quote"}}]}}"""
```

`{body}` is the abstract, or a full-text chunk. `{ortholog_aliases}` is a
comma-separated list capped at 12 names to keep the prompt small.

**c) New signature.**

```python
async def extract_interactions(
    self,
    paper_text: str,
    protein_name: str,
    ortholog_aliases: Optional[List[str]] = None,
    anchor_organism: str = "Homo sapiens",
    anchor_taxid: int = 9606,
    source_label: str = "abstract",
) -> Dict:
    """Return {"interactions": [...], "source": "llm"|"error"|"none", ...}.

    `ortholog_aliases` is what makes cross-organism names extractable: a mouse
    paper that only ever says "Trp53" still yields edges, and the anchor rule
    is satisfied because Trp53 is in the alias list.
    """
```

**d) `_parse_interactions` must accept the new fields**, coerce
`directed` strictly (`bool` only — the string `"false"` is truthy in Python and
would silently invert direction), coerce taxids to `int` with a whitelist
falling back to `anchor_taxid`, and build `Evidence` objects. Keep the existing
anchor check (`protein_name.upper() not in (source.upper(), target.upper())` →
drop) but widen it to the alias list.

**e) `generate_protein_description` — unchanged**, except it must now receive
`protein.protein_name` rather than `gene_name` (a protein *name* prompt is more
reliable than a bare symbol), and must pass `organism`.

**f) Keep the retry ladder, the pacing lock, and `NOT_CONFIGURED_MESSAGE`
verbatim.** They are load-bearing. Add one thing: a per-call
`PROTEIN_LLM_JSON_RETRIES` is unnecessary; the existing 3-shape ladder plus
`max_retries=5` on 429 is enough.

### 9.5 `app/services/graph_service.py`

**a) This is where rule 1 becomes mechanical.**

```python
def build_graph(self, central_protein, interactions, depth=2,
                active_filters=None, levels: Dict[str,int] = None,
                evidence_index=None) -> Dict:
```

New behaviour:

1. Drop any edge with no evidence. If `evidence_index` is supplied (a
   `{edge_id: n_evidence}` map from the DB), any edge whose count is 0 is
   **discarded and counted** in `stats["edges_dropped_without_evidence"]`. This
   is the enforcement of "only directly-evidenced edges" — a bug elsewhere that
   invents an edge gets caught here rather than shipped to the user.
2. BFS to assign levels over the final edge set. If `levels` is supplied by the
   crawler (it has better information because it knows discovery order), use it,
   but still verify it is a valid BFS layering and fall back to computing it if
   not.
3. For every emitted edge, compute `level_skipping = abs(level(u) - level(v)) > 1`
   and emit it as a field. Count them in `stats`.
4. Edge `id` = `"|".join(interaction.key())`. The key is now direction-aware, so
   `MDM2|TP53|regulatory` and `TP53|MDM2|regulatory` are distinct ids and
   vis-network will not throw "Cannot add item: item with id X already exists".
   That crash mode is why the type was in the id in the first place; keeping it
   plus direction is strictly stronger.
5. Edge payload gains: `directed`, `subtypes`, `primary_subtype`,
   `interaction_label` (unchanged), `taxon_a`, `taxon_b`, `level_skipping`,
   `conflicts`, and `evidence` (a list, not just a PMID count).
6. Node payload gains: `taxid`, `organism`, `ortholog_group`,
   `ortholog_species` (a list of `{taxid, organism, gene_name, accession}` from
   the DB), `support_count`, `level`. Keep `level`, `color`, `size`, `is_central`.
7. **Do not add a `title` tooltip containing a path.** The current
   `_neighbour_tooltip` shows one paper's quote; extend it to show the edge type,
   direction, subtype, taxids and up to 3 evidence quotes.

**b) `LEVEL_COLORS` / `LEVEL_SIZES` are 6 entries and `depth` is clamped to 5**
(`graph_service.py:54`). With real crawling, depth is clamped to
`CrawlParams.max_depth` (default 3) *before* the graph is built, and the colour
array must be indexed safely with `min(level, len-1)` — it already does, at
lines 123-124. Keep that.

**c) Stats to emit** (all consumed by the UI and by §14's checks):

```json
{"central_protein", "uniprot_id", "depth", "requested_depth", "depth_capped",
 "node_count", "edge_count", "directed_edge_count", "level_skipping_edge_count",
 "edges_dropped_without_evidence", "interactions_considered",
 "interactions_after_filter", "active_filters", "nodes_at_level",
 "subtype_histogram", "truncations"}
```

### 9.6 `app/routes.py`

Replace `collect_interactions()` with a thin wrapper that delegates to
`CrawlService.run()` at `depth=1` for the legacy endpoints, and add the async
endpoints. `MAX_PAPERS` becomes `CrawlParams.max_papers_per_node`.
`MAX_ABSTRACTS_FOR_EXTRACTION` becomes
`PROTEIN_LLM_MAX_ABSTRACTS_PER_NODE` and must be **reported per node** as
`papers_extracted / papers_fetched` — today 30 of 50 fetched papers are silently
discarded and nobody is told.

Keep the whole existing `warning` / `severity` block (`routes.py:343-372`) and
extend it with: unconfigured LLM, PubMed failure, zero PMIDs, extraction errors,
`truncations` non-empty (severity `warning`), and `unresolved_names` non-empty
(severity `warning`). Rule: **the response must never imply completeness when a
cap was hit.**

### 9.7 `app/utils/validation.py`

Add:

```python
def check_no_derived_edges(graph: Dict, interaction_ids: Set[str]) -> Dict:
    """Every edge in the graph must correspond to a stored interaction.

    `interaction_ids` is the set of edge ids the crawler actually produced.
    Any graph edge outside it was fabricated by the builder - the exact failure
    mode rule 1 forbids. Returns {"ok", "derived_edges": [...], "missing_from_store": [...]}.
    """

def check_edge_directionality(graph: Dict) -> Dict:
    """Report directed vs undirected edges, and flag any directed edge whose
    key was produced by the sorted (undirected) branch - a sign the old
    key() is still in use somewhere.
    """

def check_ortholog_uniqueness(graph: Dict) -> Dict:
    """No two nodes may share an ortholog_group. This is the D5 check that
    replaces the current accession-based duplicate check, which cannot see
    across organisms.
    """
```

Re-point `/api/validate/graph` at all three. Its current 200/409 contract is
fine; extend the report rather than the status codes.

---

## 10. API contract

### 10.1 New: async crawl

```
POST /api/crawl
  body: { "query" | "protein_id", "depth": 2,
          "max_nodes_per_level": 10, "max_papers_per_node": 50,
          "max_llm_calls": 200, "max_wall_clock_s": 180,
          "fulltext_top_n": 10, "use_fulltext": true,
          "filters": ["regulatory"] | null,
          "merge_into_job_id": "<job_id>" | null }
  202 -> { "job_id", "status": "queued", "params": {...}, "poll": "/api/crawl/<job_id>" }
  400 -> { "error": "..." }
  404 -> { "error": "Could not resolve '<q>' to a protein", "hint": "..." }
```

```
GET /api/crawl/<job_id>
  200 -> { "job_id", "status", "progress": {...}, "error": null,
           "created_at", "updated_at", "elapsed_s" }

GET /api/crawl/<job_id>/result
  202 -> { "job_id", "status": "running", "progress": {...} }     # not ready
  200 -> { "job_id", "status": "done",
           "nodes": [...], "edges": [...],
           "papers": [{"pmid","title","year","url"}],
           "meta": { ...all CrawlResult fields... },
           "stats": { ...graph stats... },
           "unify": { ...UnifyReport... },
           "truncations": [...],
           "warning": "...", "severity": "ok|warning|error" }

DELETE /api/crawl/<job_id>
  200 -> { "job_id", "status": "cancelled" }
```

### 10.2 Changed: `/api/graph`

Kept for API compatibility and used by `/api/validate/graph`. It now runs the
crawl **synchronously at `depth=1`** and returns the full result with HTTP 200
— but only when the node is already extracted (a cache hit) or the caller
explicitly opts in:

```
POST /api/graph?sync=1
  body: { "protein_id", "depth": 1, ... }   # depth > 1 is rejected with 400
  200 -> graph payload
  400 -> { "error": "Depth > 1 requires the async crawl endpoint. Use POST /api/crawl." }
```

Without `?sync=1` it behaves as before for `depth == 1` and returns
`202 {job_id}` for `depth > 1`, so no existing caller silently blocks for
minutes.

### 10.3 Changed: `/api/interactions`

Body gains `include_evidence: true` (default true) and `depth`. Response gains
`interactions[].evidence[]`, `interactions[].directed`, `interactions[].subtypes`,
and `unify`.

### 10.4 New: cache introspection

```
GET  /api/cache/stats -> { "l1": {...}, "durable": {"papers", "interactions",
                            "interaction_evidence", "node_interactions",
                            "ortholog_groups", "protein_aliases", "jobs"},
                            "node_extractions": n }
DELETE /api/cache?scope=l1|durable|all   (default l1, preserving today's behaviour)
```

### 10.5 Response: the graph payload

```json
{
  "job_id": "…",
  "nodes": [{
    "id": "TP53", "label": "TP53", "level": 0, "is_central": true,
    "uniprot_id": "P04637", "gene_name": "TP53", "title": "…",
    "taxid": 9606, "organism": "Homo sapiens", "ortholog_group": "GRP:9f2c…",
    "ortholog_species": [{"taxid":9606,"organism":"Homo sapiens","gene_name":"TP53","accession":"P04637"},
                         {"taxid":10090,"organism":"Mus musculus","gene_name":"Trp53","accession":"P02340"}],
    "support_count": 41, "color": "#FF6B6B", "size": 30
  }],
  "edges": [{
    "id": "MDM2|TP53|regulatory", "from": "MDM2", "to": "TP53",
    "label": "Regulatory · Inhibition", "title": "…",
    "interaction_type": "regulatory", "subtypes": ["inhibition"],
    "primary_subtype": "inhibition", "directed": true, "level_skipping": false,
    "taxon_a": 9606, "taxon_b": 9606, "group_a": "GRP:1a…", "group_b": "GRP:9f2c…",
    "paper_count": 7, "papers": ["123", "456"],
    "evidence": [{"pmid":"123","source":"llm_abstract","context":"MDM2 ubiquitinates p53…",
                  "taxon_a":9606,"taxon_b":9606,"ortholog_alias_used":null}],
    "conflicts": []
  }],
  "papers": [{"pmid":"…","title":"…","year":"2021","url":"…"}],
  "meta": {
    "params": {...}, "budget_used": {"llm_calls": 88, "papers_fetched": 412,
                                     "fulltext_fetched": 23, "extraction_cache_hits": 3,
                                     "elapsed_s": 96.4},
    "truncations": [{"reason":"max_nodes_per_level","level":1,
                     "candidates":34,"kept":10,"dropped":["BRCA1","…"]}],
    "unify": {"endpoints_rewritten": 61, "self_loops_dropped": 4,
              "intra_cluster_dropped": 2, "edges_merged": 9, "direction_flips": 1,
              "distinctness_checks_run": 122, "unresolved_names": ["XYZ1"],
              "clusters_touched": 28},
    "per_node": [{"node_id":"TP53","level":0,"papers_fetched":50,
                  "papers_extracted":20,"papers_with_fulltext":10,
                  "llm_calls":20,"cache_hit":false,"edges":37,"ms":41200}],
    "llm": {...}, "llm_status": "ready"
  },
  "stats": { ...§9.5 stats... },
  "warning": "…", "severity": "ok|warning|error"
}
```

---

## 11. Frontend changes

### 11.1 `services/api.js`

Add:

```js
export const startCrawl = async (params) => { /* POST /api/crawl → {job_id} */ };
export const getCrawlStatus = async (jobId) => { /* GET /api/crawl/<id> */ };
export const getCrawlResult  = async (jobId) => { /* GET /api/crawl/<id>/result */ };
export const cancelCrawl    = async (jobId) => { /* DELETE /api/crawl/<id> */ };
export const getCacheStats  = async () => { /* GET /api/cache/stats */ };

/** Poll a crawl to completion. onProgress receives the progress object.
 *  Backs off 1s → 2s → 4s, caps at 5s, and gives up after maxWaitMs. */
export const pollCrawl = async (jobId, onProgress, { intervalMs=1000, maxWaitMs=900000 } = {}) => { ... };
```

`getGraph` stays for `depth === 1` only.

### 11.2 `context/AppContext.js`

Add to `initialState`:

```js
job: { id: null, status: 'idle', progress: null, error: null, startedAt: null },
caps: { maxNodesPerLevel: 10, maxPapersPerNode: 50, maxLlmCalls: 200,
        fulltextTopN: 10, useFullText: true },
severity: 'ok',
```

New actions: `SET_JOB`, `SET_JOB_PROGRESS`, `SET_JOB_ERROR`, `CLEAR_JOB`,
`SET_CAP`, `SET_SEVERITY`.

Replace the `CircularProgress` in `App.js` with `<CrawlProgress/>`, which shows
level, nodes done/total, LLM calls used/allowed, edges found, and a cancel
button — so a two-minute crawl does not look like a hang.

### 11.3 `components/GraphVisualization.js`

- **Per-edge arrows.** The current global `edges: { arrows: 'to' }` puts an arrow
  on every edge, which is meaningless for a symmetric binding. Move it to the
  DataSet per item: `directed ? { to: { enabled: true } } : { to: { enabled: false } }`.
- **Edge label** = `interaction_label` + (` · ` + `primary_subtype` when present).
- **Edge colour by subtype**, with a stable hash so the same subtype is always
  the same colour. `dashes: true` for `level_skipping` edges.
- Keep `dedupeById` — the new direction-aware ids make collisions less likely,
  but vis-network still throws on duplicates and that guard is cheap.
- Click handler: unchanged semantics (edge wins over node).
- Add a legend: 4 display types + the subtype palette + a "dashed = skips a level"
  key, so the direction arrows and the level-skipping dashes are not cryptic.

### 11.4 `components/SearchBar.js`

- Depth slider: `1 … CrawlParams.max_depth` (default 3, not 5 — at depth 5 a real
  crawl is unbounded in practice).
- Add the cap sliders from `state.caps`.
- Show a live estimate: `≈ min(maxNodesPerLevel, frontier) × maxPapersPerNode`
  LLM calls, and disable Search when it exceeds `maxLlmCalls`, with the number
  shown. This makes the cap visible before the user spends money.
- Replace the one-shot `getGraph` with `startCrawl` + `pollCrawl`.

### 11.5 `components/ProteinDetailsPanel.js`

Change "Load Connections" to **"Expand in graph"**, which calls
`POST /api/crawl` with `merge_into_job_id: state.job.id` and
`protein_id: state.selectedNode.id`. The crawler seeds `visited`, `levels` and
`edges` from the parent graph (§8.5 `merge_into`), so the existing structure is
preserved and the new ring is appended. This is what `todo` line 14 asks for
("If you click on a node you get the option to load the new proteins
connections") and is the one place the current code re-roots instead of
expanding.

Also display `ortholog_species` for the clicked node, so a user can see that
`Trp53` was folded into this node and which species it came from.

### 11.6 `components/PaperListPanel.js`

Currently lists bare PMIDs with the abstract behind a hover tooltip. Change each
row to show: title, year, the **evidence quote** with the protein names
highlighted, an `abstract` / `full text` chip from `evidence.source`, and the
taxid. This is the answer to `todo` line 11 — "you will see (on the left) the
papers this binding is mentioned in (where the info is gotten from)". The
provenance chip is the "where the info is gotten from" part and does not exist
today.

### 11.7 `components/FilterControls.js`

Keep the 4 checkboxes (D6). Add a subtype row beneath them that filters on
`primary_subtype` client-side — no refetch, since all subtypes are already in the
payload.

---

## 12. Environment variables

Append to `.env.example`, with the reasoning inline as the existing file does:

```ini
# ---------------------------------------------------------------- Crawl
# Maximum BFS levels. Depth 3 on a hub protein is already large; 5 is not
# reachable in practice.
PROTEIN_CRAWL_MAX_DEPTH=3
# Neighbours expanded per level, ranked by evidence count. The root is exempt.
PROTEIN_CRAWL_MAX_NODES_PER_LEVEL=10
PROTEIN_CRAWL_MAX_PAPERS_PER_NODE=50
# Total LLM calls for one crawl. One call per abstract, plus one per full-text
# chunk. This is the real cost dial.
PROTEIN_CRAWL_MAX_LLM_CALLS=200
# Wall-clock guard. Exceeding it truncates the crawl and is reported.
PROTEIN_CRAWL_MAX_WALL_CLOCK_S=180

# Abstracts actually extracted per node. Papers fetched but not extracted are
# counted and reported, never silently dropped.
PROTEIN_LLM_MAX_ABSTRACTS_PER_NODE=20

# ---------------------------------------------------------------- Full text
PROTEIN_USE_FULLTEXT=true
# How many of a node's most-cited/relevant papers get full text. Full text is
# 10-30x the tokens of an abstract.
PROTEIN_FULLTEXT_TOP_N=10
PROTEIN_FULLTEXT_MAX_CHARS=40000
PROTEIN_FULLTEXT_CHUNK_CHARS=6000
PROTEIN_FULLTEXT_CHUNK_MAX=4

# ---------------------------------------------------------------- Identity
# The workflow specifies protein names, NOT gene names: "CAT"[tiab] returns
# 4,972 hits versus 1,335 for "Catalase"[tiab].
PROTEIN_SEARCH_INCLUDE_GENE_SYMBOLS=false
PROTEIN_SEARCH_MAX_TERMS=6
PROTEIN_ORTHOLOG_MAX_ORGANISMS=12
# Order in which cross-species entries are kept. Bounds the UniProt query.
PROTEIN_ORTHOLOG_SPECIES_PRIORITY=9606,10090,10116,7955,7227,6239,4932,3702,9913,562,8355,4896
# Set false to make cross-species names visible as separate nodes (D5 says no).
PROTEIN_MERGE_ORTHOLOGS=true

# ---------------------------------------------------------------- Runtime
# Bumping this invalidates exactly the LLM-extraction cache rows and nothing else.
PROTEIN_PROMPT_VERSION=v2
PROTEIN_DB_PATH=data/protein_friend_finder.db
PROTEIN_MAX_CONCURRENT_JOBS=1
PROTEIN_GRAPH_INLINE_TIMEOUT_S=0
```

**Delete** `PROTEIN_LLM_ALLOW_PLACEHOLDER_FALLBACK` from `.env` — it is read
nowhere (gap 6.3) and its presence implies a fabrication fallback that was
deliberately deleted.

`requirements.txt` needs **no change**; `sqlite3` is stdlib.

---

## 13. Implementation order

Each phase is independently runnable and leaves the app working.

| Phase | Work | Files |
|---|---|---|
| **1** | SQLite layer + `Paper`/`Protein` extensions + `SimpleCache` L2 wiring | `app/services/db.py` (new), `app/models/paper.py`, `app/models/protein.py`, `app/utils/cache.py` |
| **2** | Interaction model: `subtypes`, `directed`, taxons, `Evidence`, new `key()`/`merge()` | `app/models/interaction.py`, `app/models/evidence.py` (new) |
| **3** | Name expansion + ortholog service (steps 1's names and step 3's identity) | `app/services/name_expansion.py` (new), `app/services/ortholog_service.py` (new), `app/services/protein_resolver.py` |
| **4** | Prompt v2 + new parse fields | `app/services/llm_service.py` |
| **5** | `build_query(NameSet)`, PMCID parsing, `fetch_full_text`, `chunk_for_llm` | `app/services/pubmed_service.py` |
| **6** | `CrawlService` — the 1→2→3→4 loop (this is the critical path) | `app/services/crawl_service.py` (new) |
| **7** | `JobService` — one thread, one event loop, SQLite-backed job state | `app/services/job_service.py` (new), `app/__init__.py` |
| **8** | Graph builder hardening + the three validators | `app/services/graph_service.py`, `app/utils/validation.py` |
| **9** | Routes: `/api/crawl*`, `/api/graph?sync=1`, cache stats, extended warnings | `app/routes.py` |
| **10** | Frontend: async crawl, progress, per-edge arrows, subtypes, provenance | all of `frontend/src/` |
| **11** | `.env.example`, delete the dead flag, end-to-end acceptance run | `.env`, `.env.example` |

Phases 1–5 are all persistence and extraction quality; **phase 6 is what turns
the depth slider from a no-op into a crawl**, and it depends on 1–5. Phase 7 is
what makes a multi-minute crawl survivable over HTTP.

---

## 14. Acceptance criteria

Each is mechanically checkable. `pytest` is not currently a dependency; add it
and a `tests/` directory as part of phase 1.

### 14.1 Depth actually crawls (the critical regression)

```
GIVEN a live LLM backend and PROTEIN_CRAWL_MAX_LLM_CALLS=200
WHEN  POST /api/crawl {protein_id: "P04637", depth: 2, max_nodes_per_level: 3}
THEN  the result contains nodes at level 0, level 1 AND level 2
AND   stats.nodes_at_level == {"0": 1, "1": >=1, "2": >=1}
AND   the level-1 and level-2 node sets are DISJOINT
AND   the number of level-2 nodes <= 3 * max_nodes_per_level
```

Regression guard for gap 4.1: assert the level-2 set is non-empty. If this test
passes with `depth: 1` too, the crawl is not iterating.

### 14.2 No derived edges (rule 1)

```
GIVEN a completed crawl with result set E and evidence index V
WHEN  for every edge e in graph.edges: assert e.id in E
AND   for every edge e in graph.edges: assert V[e.id] >= 1
THEN  no edge exists that was not directly extracted from a paper
```

The second assertion is the one that matters: an edge with zero supporting
papers must never reach the user. `check_no_derived_edges` implements it.

### 14.3 Directionality survives end to end

```
GIVEN an LLM response containing
      {"source_protein":"MDM2","target_protein":"TP53","directed":true,
       "interaction_type":"regulatory","subtype":"ubiquitination"}
WHEN  it is stored, then re-read from SQLite, then rendered
THEN  interactions.subtype for that edge includes "ubiquitination"
AND   edge.directed is true
AND   edge.from == "MDM2" and edge.to == "TP53"
AND   the reverse pair, if also extracted, is a SEPARATE edge with a different id
```

### 14.4 Subtype is preserved, not collapsed

```
GIVEN a response with interaction_type="regulatory", subtype="inhibition"
THEN  edge.interaction_type == "regulatory"     # still one of the 4 (D6)
AND   "inhibition" in edge.subtypes
AND   edge.primary_subtype == "inhibition"
AND   no normalisation step maps "inhibition" to "physical_binding"
```

### 14.5 Cross-organism unification (D5)

```
GIVEN a search for human p53 (P04637)
WHEN  the name expansion runs
THEN  the ortholog group contains P04637, P02340 (Trp53), P10361 (Tp53), P79734 (tp53)
AND   every node in the graph has a distinct ortholog_group
AND   "Trp53" and "TP53" never appear as two separate nodes
AND   each edge built from a mouse paper carries taxon 10090
```

### 14.6 Full text is used and bounded

```
GIVEN use_fulltext=true, fulltext_top_n=10, and a node with 50 papers
THEN  meta.per_node[0].papers_with_fulltext <= 10
AND  >= 1 paper has a non-null fulltext when the node's papers have PMCIDs
AND  every llm call is attributable: per_node[0].llm_calls ==
      abstracts_extracted + fulltext_chunks_extracted
AND  total llm_calls across all nodes <= params.max_llm_calls
```

### 14.7 Caps are reported, never silent

```
GIVEN max_nodes_per_level=3 and a level with 30 candidates
THEN  meta.truncations contains {reason: "max_nodes_per_level", level: 1,
                                 candidates: 30, kept: 3, dropped: [...]}
AND  graph.severity == "warning"
AND  graph.warning mentions the truncation
```

### 14.8 The cache actually caches, and is correctly keyed

```
WHEN  the same crawl is submitted twice with identical params
THEN  run 2 issues 0 LLM calls
AND  run 2's result is deep-equal to run 1's
WHEN  the process is restarted between the two runs
THEN  run 2 still issues 0 LLM calls
WHEN  PROTEIN_PROMPT_VERSION is bumped and the same crawl is re-run
THEN  LLM calls are issued again
```

The third assertion is what gap 2.3 is about; it fails against the current
in-memory cache.

### 14.9 The crawl is cancellable and survives restart

```
GIVEN a running job
WHEN  DELETE /api/crawl/<job_id>
THEN  status becomes "cancelled" within one node's work
WHEN  the process is killed mid-job and restarted
THEN  that job reads back as "interrupted", not "running"
```

### 14.10 No fabrication, still

```
GIVEN PROTEIN_LLM_API_KEY is unset
WHEN  POST /api/crawl
THEN  202 with a job that finishes "ok" and 0 edges
AND  graph.severity == "error"
AND  graph.warning contains "will not fabricate interactions"
```

### 14.11 Graph invariants hold for a level-skipping edge

```
GIVEN the crawl produced a directly-stated edge A–C while a path A–B–C exists
THEN  that edge IS present in graph.edges
AND  its level_skipping field is true
AND  it is counted in stats.level_skipping_edge_count
```

---

## 15. Deviations, risks and dead ends

### 15.1 Known deviation: "most cited" is not most cited

D1 keeps PubMed's `sort="relevance"`. Step 1 as originally worded asked for the
**50 most cited** papers. PubMed's ESearch accepts only `relevance`, `pub_date`
and `Author` — there is no citation sort, and PubMed records no citation count.
So the delivered behaviour is "the 50 most *relevant* papers", and the spec does
not pretend otherwise.

If this is revisited, the cheapest path uses **no new dependency**, because
PubMed already exposes citation counts through ELink. Verified working:

```python
handle = Entrez.elink(dbfrom="pubmed", db="pubmed", id=pmid,
                      linkname="pubmed_pubmed_citedin")
record = Entrez.read(handle); handle.close()
# MITAB-free, but note Biopython returns a ListElement here, NOT a dict:
#   record[0]["LinkSetDb"][0]["Link"]   -> 57 entries for PMID 2943983
# citation_count = len(record[0]["LinkSetDb"][0]["Link"])
```

Cost: one extra request per PMID, so 50 requests per node, and a 50-node crawl
means 2,500 extra requests at the NCBI rate limit. It is therefore worth doing
only at the root, or only for the top 50 by relevance before re-sorting — which
gives "the most cited among the most relevant", not "the most cited". Beyond
that, Europe PMC `sort=CITED desc` is one request per node and exact; it was
declined in D1, and is recorded here only so the trade-off is on the record.

### 15.2 Risk: cost blow-up at depth 2+

50 papers/node × 1 LLM call/abstract + full-text chunks, times every expanded
node. With the default caps (`max_nodes_per_level=10`, `max_llm_calls=200`,
`max_wall_clock_s=180`) the wall-clock guard binds before the call guard on
Groq's free tier, so most depth-2 crawls will hit 180s. That is the intended
behaviour — the caps exist to make this predictable — but it means:

- The UI **must** show progress and the LLM budget in use, or a 180-second wait
  reads as a hang. `CrawlProgress` is not optional.
- `PROTEIN_LLM_MIN_INTERVAL` (0.6s) × 200 calls = 120s of pure pacing. The
  wall-clock guard and the pacing interval are the same order of magnitude;
  tune them together.
- Anything past depth 2 needs a paid key or a local model. Say so in the UI
  rather than letting the crawl silently truncate.

### 15.3 Risk: `ortholog_species` needs a taxid, and UniProt sometimes omits it

`organism_id` was in the verified field list for the protein-name search, but
`ProteinService._to_protein` currently reads `entry["organism"]["scientificName"]`
and never the numeric id. If `organism_id` is absent from a response, fall back
to a `scientificName → taxid` table covering the 12 species in
`PROTEIN_ORTHOLOG_SPECIES_PRIORITY`, and record the edge's taxon as `None` with
`unresolved_names` noting it. Never guess a taxid.

### 15.4 Risk: merging orthologs loses species-specific biology

D5 merges mouse `Trp53` into the human `TP53` node. That is what step 3 asked
for ("same protein under a different name in other organisms … unify them"), and
it is defensible for a *literature-derived interaction graph* where the point is
the protein family. It is **not** defensible for a claim about human-specific
biology. Mitigations already specified: the node lists
`ortholog_species`; every edge and every evidence row keeps its own taxid; the
`meta.per_node` stats show which organism's papers produced which edges. If a
future requirement needs strict species separation,
`PROTEIN_MERGE_ORTHOLOGS=false` splits them and the rest of the pipeline is
unchanged, because ortholog grouping is already a separate layer
(`OrthologService`) from node identity.

### 15.5 Risk: one LLM call per abstract is the bottleneck

The existing design is deliberately one call per abstract, because batching
loses attribution — you cannot say which paper an edge came from, and step 2
requires exactly that provenance. Do not batch. To cut cost instead: lower
`PROTEIN_LLM_MAX_ABSTRACTS_PER_NODE`, lower `fulltext_top_n`, or rely on the
`node_extractions` cache so a repeated crawl costs nothing. If a batched mode is
ever added, it must return a per-item `source_pmid` and fan out into one
`Evidence` row per PMID, or rule 1 becomes unverifiable.

### 15.6 Risk: reasoning models and the token ceiling

`EXTRACTION_MAX_TOKENS` is 1200 with a 4× retry (already implemented,
`llm_service.py:121-123`). The v2 prompt is longer and its JSON response is
larger, because every item now carries five extra fields. Budget roughly 1.6× the
output tokens per interaction. If `gpt-oss-120b` starts failing with "max
completion tokens reached before generating a valid json object", raise
`PROTEIN_LLM_MAX_TOKENS` before touching anything else.

### 15.7 Dead end: a single UniProt query cannot find all orthologs

`protein_name:"..."` finds orthologs whose UniProt *recommended name* matches
(exact for all vertebrates in the verified p53 result), but paralogues and
species with a completely different recommended name are missed. The
`gene_exact` cross-species query in §8.2 step 3 widens coverage but reintroduces
gene symbols into the *lookup*. The spec keeps gene symbols out of
`search_terms` while still using them for discovery, which is the closest
achievable approximation of "(not associated gene names)".

### 15.8 Dead end: `Interaction.key()` cannot stay sorted

Worth stating explicitly because it is the single most likely thing to be
"simplified" back: the moment `directed` exists, the sorted-endpoint key
destroys direction at merge time, and the graph will show `A—B` with no way to
tell which way round it is. The undirected branch keeps the sort; the directed
branch must not.

### 15.9 Not in scope

Deliberately excluded, and the spec does not quietly include them: IntAct /
STRING / BioGRID / PSICQUIC ingestion (declined, D4); citation-ranked retrieval
(§15.1); a graph database (SQLite is sufficient at this scale); user accounts
and multi-tenancy; PDF parsing for non-OA publishers (paywalled, legally and
technically messy — PMC covers the OA subset); and species-specific
interaction databases such as the fly or worm BioGRID, which would be the
natural next step if §15.4's concern ever becomes a requirement.

---

## 16. Session Recovery Guide

If this project is picked up in a new session, read this section first.

### 16.1 Current state summary

The application is a working Flask + React web app that visualizes protein-protein
interaction networks. It is **not** fully implemented per §1-15 — the full
crawl pipeline (SQLite, orthologs, directed edges, async jobs) is specified but
not yet built. What exists today is a functional prototype.

**What works today:**
- Backend: Flask server with 7 endpoints, PubMed search, UniProt protein lookup,
  LLM-based interaction extraction, graph building, caching, rate limiting
- Frontend: React + MUI dark theme, interactive vis-network graph, search,
  filter, protein details panel, paper list panel
- Logging: Full structured logging on both backend and frontend

**What does NOT work yet (per spec §1-15):**
- SQLite persistence (currently in-memory cache only)
- Ortholog cross-organism expansion
- Directed edges and subtypes
- Async background crawl jobs
- Full-text PMC fetching
- Depth slider actually crawls multiple levels (it's currently cosmetic —
  the graph is always one ring deep)

### 16.2 How to run the application

```bash
# Terminal 1 — Backend
cd C:\Users\aless\OneDrive\Desktop\Hackathon
pip install -r requirements.txt
python run.py
# Server starts on http://localhost:5000

# Terminal 2 — Frontend
cd frontend
npm install
npm start
# App opens on http://localhost:3000
```

**Required `.env` file** (already exists at `C:\Users\aless\OneDrive\Desktop\Hackathon\.env`):
```
PROTEIN_LLM_API_KEY=gsk_REDACTED
PROTEIN_LLM_ALLOW_PLACEHOLDER_FALLBACK=0
```
The backend reads `.env` on startup via `app/config.py`. Edit `.env` and restart
the backend. The frontend talks to `http://localhost:5000` (hardcoded in
`frontend/src/services/api.js`).

### 16.3 File map (current)

```
C:\Users\aless\OneDrive\Desktop\Hackathon\
├── .env                          # Environment variables (LLM key, etc.)
├── .env.example                  # Template
├── run.py                        # Entry point: app.run(debug=True, port=5000)
├── requirements.txt              # Flask, Flask-CORS, biopython, requests, python-dotenv
├── app/
│   ├── __init__.py              # create_app(), setup_logging() call
│   ├── config.py                # .env loader
│   ├── routes.py                # All HTTP endpoints + collect_interactions()
│   ├── utils/
│   │   ├── __init__.py          # Exports SimpleCache, cache, AsyncRateLimiter, setup_logging, logger
│   │   ├── logger.py            # ColouredFormatter, setup_logging() — NEW
│   │   ├── cache.py             # SimpleCache (in-memory, TTL 3600s)
│   │   └── rate_limiter.py      # AsyncRateLimiter
│   ├── models/
│   │   ├── __init__.py          # Exports Protein, Interaction, Paper
│   │   ├── protein.py           # Protein dataclass, search_terms()
│   │   ├── interaction.py       # Interaction dataclass, 4 types, normalize_type()
│   │   └── paper.py             # Paper dataclass
│   └── services/
│       ├── pubmed_service.py    # PubMed API (esearch, efetch, MEDLINE parser)
│       ├── protein_service.py   # UniProt REST API resolver
│       ├── llm_service.py       # LLM extraction + summary (Groq-compatible)
│       ├── graph_service.py     # BFS graph builder
│       └── __init__.py          # Not present (imports work via module paths)
├── frontend/
│   ├── package.json
│   └── src/
│       ├── index.js             # React entry point
│       ├── index.css            # Dark theme global styles, animations, scrollbar
│       ├── App.js               # Dark MUI theme, layout, search bar, graph, panels
│       ├── context/
│       │   └── AppContext.js    # React context: state, dispatch, useApp()
│       ├── utils/
│       │   └── logger.js        # createLogger() — per-module console loggers
│       ├── services/
│       │   └── api.js           # Axios with interceptors, API functions
│       └── components/
│           ├── SearchBar.js      # Search + depth slider
│           ├── GraphVisualization.js  # vis-network, zoom, export, legend
│           ├── ProteinDetailsPanel.js # Right drawer, protein info, load connections
│           ├── PaperListPanel.js      # Left drawer, related papers
│           └── FilterControls.js      # Interaction type filter checkboxes
└── WORKFLOW_SPEC.md             # This file
```

### 16.4 Logging implementation details

#### Backend logging (`app/utils/logger.py`)
- `ColouredFormatter` class: ANSI-coloured level names, timestamps, module names
- `setup_logging(level, show_timestamp, log_to_file)` function
- Called in `app/__init__.py` on import and `run.py` on startup
- All service modules import `logger = logging.getLogger(__name__)`
- Every method logs: entry points, cache hits/misses, API calls, results, errors
- `exc_info=True` on all error logs for full traceback in console

**Log format:** `[2024-01-15 10:30:45] INFO  pubmed_service — Searching PubMed: query='...'`

#### Frontend logging (`frontend/src/utils/logger.js`)
- `createLogger(module)` factory returns `{ info, warn, error, debug, group }`
- Each method prefixes with `🧬 ProteinFriend [ModuleName]` + emoji indicator
- Module-specific loggers exported: `apiLogger`, `searchLogger`, `graphLogger`,
  `proteinLogger`, `paperLogger`, `filterLogger`, `appLogger`, `contextLogger`
- All components and services use their dedicated logger
- **No `console.log`/`console.error` anywhere** — all use the structured logger

**Console format:** `🧬 ProteinFriend [API] ℹ️ Searching protein: "p53"`

### 16.5 Frontend design implementation details

#### Dark mode theme (`frontend/src/App.js`)
- MUI `createTheme` with `palette.mode: 'dark'`
- Background: `#0D0D1A` (deep navy-black) with subtle radial gradient glows
- Surface: `#1A1A2E` panels with `backdrop-filter: blur(20px)`
- Primary: `#6C63FF` (vibrant purple) with gradient buttons and glow effects
- Secondary: `#FF6584` (coral pink) for accents
- Text: `#E8E8F0` primary, `#A0A0B8` secondary
- All MUI components have `styleOverrides` for consistent dark styling
- Typography: Inter font family, proper letter spacing

#### Graph visualization enhancements (`frontend/src/components/GraphVisualization.js`)
- **Colour-coded nodes by depth level**: purple (#6C63FF) → teal (#4ECDC4) → gold (#F7DC6F) → lavender (#BB8FCE) → sky blue (#85C1E9)
- **Glowing shadows** on nodes matching their color
- **Bold labels** with dark stroke (#0D0D1A) for readability
- **Center node larger** (36px) with progressive sizing
- **Cubic Bezier edges** with purple tint
- **Custom CSS tooltips** with dark background and blur
- **Depth legend** at bottom-left showing color-to-level mapping
- **Zoom/Reset/Export** controls at top-right
- Custom node styling via `Network` DataSet with shadow, borderWidth, font config

#### Component styling patterns
- All panels use `Paper` with dark background (`rgba(26, 26, 46, 0.8)`) and `backdrop-filter: blur(20px)`
- Consistent border: `1px solid rgba(255,255,255,0.06)`
- Search bar: glass-morphism card with purple search icon
- Filter controls: styled checkboxes, active filter chips, depth display
- Protein/Paper panels: right/left drawers with slide-in animations, uppercase section labels
- Buttons: gradient backgrounds, hover lift effects, consistent border-radius 10px

#### CSS animations (`frontend/src/index.css`)
- `@keyframes fadeIn`, `slideInRight`, `slideInLeft`, `spin`, `pulse`
- Custom scrollbar: 6px width, purple-tinted thumb
- `::selection` styled with purple tint
- `@keyframes spin` for the loading spinner (replaces MUI CircularProgress)

### 16.6 Known issues and limitations

1. **Depth slider is cosmetic** (gap 4.1 in spec). `get_graph` always does a single
   `collect_interactions(root)` call. The graph is always one ring deep regardless
   of depth setting. Fix requires implementing `CrawlService` (§8.5).

2. **`PROTEIN_LLM_ALLOW_PLACEHOLDER_FALLBACK=0`** in `.env` is dead config —
   read nowhere in the code. The placeholder backend was deliberately removed.
   Safe to remove from `.env`.

3. **`run_async` is `asyncio.run` per Flask request** — creates a new event loop
   every time. This works but is inefficient. The `AsyncRateLimiter` and
   `llm_service._pace_lock` work around this but it's a known issue (gap 6.1).

4. **In-memory cache only** — `SimpleCache` is a dict that's lost on restart.
   No SQLite persistence.

5. **No full-text fetching** — only abstracts are read. `MAX_ABSTRACTS_FOR_EXTRACTION`
   defaults to 20, so 30 of 50 fetched papers are silently discarded.

6. **Frontend MUI theme** has `success`, `warning`, `error`, `info` as
   `{ main: '#...' }` objects — this was required because MUI's `augmentColor`
   needs a `main` property. Plain string values cause the error reported
   earlier.

### 16.7 What to implement next (per WORKFLOW_SPEC.md)

The highest-priority remaining work, in order:

1. **Phase 6: `CrawlService`** — Makes the depth slider actually work by
   implementing BFS iteration (§8.5, §14.1)
2. **Phase 1: SQLite layer** — Persistence so cache survives restart (§7, §14.8)
3. **Phase 2: Interaction model** — Directed edges, subtypes, Evidence (§6, §14.3-14.4)
4. **Phase 3: Name expansion** — Cross-organism ortholog lookup (§8.2, §14.5)
5. **Phase 10: Frontend async crawl** — Progress display, polling (§11, §11.2-11.4)

### 16.8 Environment variables reference

Current `.env` contains:
```
PROTEIN_LLM_API_KEY=gsk_REDACTED
PROTEIN_LLM_ALLOW_PLACEHOLDER_FALLBACK=0  # DEAD — not read anywhere
NCBI_EMAIL=
NCBI_API_KEY=
```

Used by code:
- `PROTEIN_LLM_API_KEY` → `llm_service.py` builds Groq backend
- `PROTEIN_LLM_BACKEND` / `PROTEIN_LLM_MODEL` / `PROTEIN_LLM_BASE_URL` → optional
- `PROTEIN_LLM_ABSTRACT_CHARS` → default 1500 chars per abstract
- `PROTEIN_LLM_MAX_TOKENS` → default 1200 max tokens per extraction
- `PROTEIN_LLM_MIN_INTERVAL` → default 0.6s between LLM calls
- `PROTEIN_LLM_MAX_ABSTRACTS` → default 20 per node (legacy name)
- `NCBI_EMAIL` → Entrez email for PubMed API
- `NCBI_API_KEY` → Optional NCBI API key

### 16.9 Key file contents reference

#### `app/utils/logger.py` — Key functions
```python
setup_logging(level=logging.INFO, show_timestamp=True)  # Called on import
# logger = logging.getLogger(__name__)  # Use in each module
```

#### `app/__init__.py` — Startup sequence
```python
from app.utils.logger import setup_logging
setup_logging(level=logging.INFO)  # Configures root logger
# Then: Flask app creation, CORS, blueprint registration
```

#### `frontend/src/utils/logger.js` — Logger creation
```javascript
const logger = createLogger('MyModule');  // Returns { info, warn, error, debug, group }
logger.info('Message', ...args);   // console.info with prefix
logger.error('Message', err);       // console.error with prefix
logger.debug('Message', ...args);   // console.debug with prefix
```

#### `app/routes.py` — Endpoint list
```
POST   /api/search          — Resolve protein by name/symbol/accession
GET    /api/protein/<id>    — Detailed protein info
POST   /api/interactions    — Get interactions for a protein
GET    /api/papers/<pmid>   — Get paper details
POST   /api/graph           — Build interaction graph
GET    /api/health          — Health check
DELETE /api/cache           — Clear cache
```

---

## End of WORKFLOW_SPEC.md

*Last updated: 2026-09-26. All claims verified by reading the actual source files.*
*Structure logging implemented across all Python backend modules and React frontend components.
Dark mode redesign applied to all frontend components.*
