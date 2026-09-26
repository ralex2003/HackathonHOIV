import asyncio
import os
import logging
from typing import List, Tuple, Set, Dict
from collections import deque

from flask import Blueprint, jsonify, request

from app.models.interaction import Interaction
from app.models.paper import Paper
from app.models.protein import Protein
from app.services.graph_service import GraphService
from app.services.llm_service import LLMService
from app.services.protein_resolver import ProteinResolver
from app.services.protein_service import ProteinService
from app.services.pubmed_service import PubMedService
from app.utils.cache import cache
from app.utils.validation import check_graph_protein_uniqueness

logger = logging.getLogger(__name__)

bp = Blueprint("api", __name__)

pubmed_service = PubMedService()
protein_service = ProteinService()
llm_service = LLMService()
graph_service = GraphService()
protein_resolver = ProteinResolver()

MAX_PAPERS = 5  
MAX_ABSTRACTS_FOR_EXTRACTION = int(
    os.environ.get("PROTEIN_LLM_MAX_ABSTRACTS", "5")
)
# Total cap on LLM calls across ALL levels of the crawl.  
# At 3 abstracts per node and 2 levels, that's ~9 calls max for depth=2.
# New interactions kept per crawled node. Deliberately a PER-NODE budget: a
# node discovered at level 3 gets the same allowance as the root, so `depth`
# actually controls how much of the graph gets explored.
#
# This used to be a single global cap shared across the whole crawl. The root
# ate the first slice, the next few level-1 nodes ate the rest, and every
# remaining node returned zero interactions -- which made the depth slider
# almost meaningless past depth 1.
MAX_EDGES_PER_NODE = int(os.environ.get("PROTEIN_MAX_EDGES_PER_NODE", "5"))

# Clamp for the user-supplied value so one request cannot ask for an
# unbounded crawl.
EDGES_PER_NODE_MIN = 1
EDGES_PER_NODE_MAX = 25

# Absolute ceiling on LLM calls for one entire crawl. This is a runaway-cost
# backstop, NOT the primary limiter -- that job belongs to
# MAX_EDGES_PER_NODE. It is intentionally generous so recursion can finish; if
# it trips, the response says so via meta["truncations"].
MAX_LLM_CALLS = int(os.environ.get("PROTEIN_LLM_MAX_TOTAL_CALLS", "300"))

def run_async(coro):
    return asyncio.run(coro)


def _json_body() -> dict:
    return request.get_json(silent=True) or {}


async def _crawl_node(
    protein: Protein,
    max_papers: int,
    edge_limit: int,
    llm_budget_ref: dict,
    seen: Set[str],
) -> Tuple[List[Interaction], List[Paper], dict]:
    """Fetch papers for one protein and extract interactions from their abstracts.

    `edge_limit` caps how many NEW proteins this node may contribute to the
    graph. `seen` is the shared set of symbols already in the graph; it is
    mutated in place with anything this node discovers, so sibling nodes
    cannot waste budget re-discovering the same protein.

    Edges back into the existing graph are kept (they are real evidence) but
    do not consume budget -- only genuinely new partners do.

    Returns (interactions, papers, meta) for this single node.
    Respects llm_budget_ref and returns empty results if the budget is exhausted.
    """
    terms = protein.search_terms()
    if not terms:
        # Same keys as the normal path, so callers can read edges_kept /
        # edges_dropped_by_cap without a KeyError on a skipped node.
        return [], [], {
            "query": None,
            "total_matches": 0,
            "skipped": "no_search_terms",
            "edges_kept": 0,
            "edge_limit": edge_limit,
            "edges_offered": 0,
            "edges_dropped_by_cap": 0,
        }

    # edge_limit is part of the key: a node crawled with a limit of 5 must not
    # be served from a cache entry built with a limit of 25.
    cache_key = (
        f"node:{protein.uniprot_id}:{max_papers}:{edge_limit}:"
        f"{llm_service.is_configured}"
    )
    cached = cache.get(cache_key)
    if cached is not None:
        logger.info("Node cache HIT for %s", protein.node_id)
        # A HIT must reproduce the `seen` mutation the original crawl made,
        # otherwise the caller sees `visited - before == empty` and builds an
        # empty frontier -- i.e. depth>1 silently collapses to depth=1 on
        # every repeat query. New cache entries store the claimed symbols as
        # a 4th tuple element; older 3-tuple entries fall back to deriving
        # them from the cached edges.
        if len(cached) == 4:
            interactions_c, papers_c, meta_c, new_symbols_c = cached
        else:
            interactions_c, papers_c, meta_c = cached
            symbol_c = protein.node_id
            new_symbols_c = []
            for inter_c in interactions_c:
                far_c = (
                    inter_c.target_protein
                    if inter_c.source_protein == symbol_c
                    else inter_c.source_protein
                )
                if far_c and far_c != symbol_c and far_c not in new_symbols_c:
                    new_symbols_c.append(far_c)
        fresh = [s for s in new_symbols_c if s and s not in seen]
        for s in fresh:
            seen.add(s)
        meta_hit = dict(meta_c)
        meta_hit["cache_hit"] = True
        # `new_partners` describes this crawl, not the original one: symbols
        # already claimed by an earlier sibling contribute no fresh frontier.
        meta_hit["new_partners"] = len(fresh)
        return interactions_c, papers_c, meta_hit

    logger.info("Node crawl: protein='%s', searching PubMed...", protein.node_id)
    query = pubmed_service.build_query(terms)
    pmids = await pubmed_service.search_papers(query, max_results=max_papers)
    papers = await pubmed_service.fetch_multiple_papers(pmids)

    symbol = protein.node_id
    merged: dict = {}
    used = 0
    sources: dict = {}
    offered = 0          # interactions the LLM proposed, before capping
    novel_kept = 0       # kept edges whose far endpoint was NOT already seen
    redundant_kept = 0   # kept edges pointing back into the existing graph
    dropped_by_cap = 0   # proposed but over this node's novel budget
    new_symbols: Set[str] = set()

    for paper in papers:
        if not paper.abstract:
            continue
        # Budget spent: stop when this node has found `edge_limit` proteins
        # that were NOT already in the graph. Edges pointing back at
        # already-known proteins are still kept (they are real evidence) but
        # they do not consume budget, because they cannot expand the frontier.
        #
        # This is the whole point: a per-node budget spent on *any* edge gets
        # swallowed by re-links to existing nodes -- measured 5 novel vs 25
        # redundant -- so the graph never grew past one ring no matter how
        # high the depth slider went.
        if novel_kept >= edge_limit:
            break
        if used >= MAX_ABSTRACTS_FOR_EXTRACTION:
            logger.info(
                "Node %s hit abstract cap (%d) with only %d/%d NEW partners",
                protein.node_id, MAX_ABSTRACTS_FOR_EXTRACTION, novel_kept, edge_limit,
            )
            break
        if llm_budget_ref["count"] >= llm_budget_ref["max"]:
            logger.warning("LLM budget exhausted at node %s", protein.node_id)
            break

        used += 1
        llm_budget_ref["count"] += 1
        logger.debug(
            "Extracting interactions from PMID=%s for %s "
            "(%d/%d max_llm=%d, %d/%d new partners so far)",
            paper.pmid, protein.node_id,
            llm_budget_ref["count"], llm_budget_ref["max"], llm_budget_ref["max"],
            novel_kept, edge_limit,
        )
        extracted = await llm_service.extract_interactions(paper.abstract, symbol)
        source = extracted.get("source", "unknown")
        sources[source] = sources.get(source, 0) + 1
        for item in extracted.get("interactions", []) or []:
            src = item.get("source_protein")
            tgt = item.get("target_protein")
            if not src or not tgt or src == tgt:
                continue
            offered += 1
            interaction = Interaction(
                source_protein=src,
                target_protein=tgt,
                interaction_type=Interaction.normalize_type(
                    item.get("interaction_type", "")
                ),
                papers=[paper],
                context=item.get("context"),
            )
            key = interaction.key()
            if key in merged:
                # Free: no extra LLM call, and it strengthens the evidence on
                # an edge we already kept. Always allowed.
                merged[key].merge(interaction)
                continue

            # Novelty test: which endpoint is the neighbour, from this node?
            far = tgt if src == symbol else src
            already = far in seen
            if already:
                # A re-link into the existing graph. Keep it -- it is genuine
                # evidence and costs nothing -- but do not spend budget on it.
                merged[key] = interaction
                redundant_kept += 1
            elif novel_kept < edge_limit:
                merged[key] = interaction
                novel_kept += 1
                new_symbols.add(far)
                # Claim it immediately so a sibling node cannot spend its
                # budget re-discovering the same protein.
                seen.add(far)
            else:
                # Over this node's novel-partner budget. Counted and reported
                # rather than silently dropped.
                dropped_by_cap += 1

    meta = {
        "query": query,
        "total_pmids": len(pmids),
        "papers_fetched": len(papers),
        "papers_with_abstract": used,
        "extraction_sources": sources,
        "llm_calls_used": llm_budget_ref["count"],
        "edges_kept": len(merged),
        "edge_limit": edge_limit,
        "new_partners": novel_kept,
        "redundant_edges": redundant_kept,
        "edges_offered": offered,
        "edges_dropped_by_cap": dropped_by_cap,
    }
    result = (list(merged.values()), papers, meta)
    # Only cache a result that actually spent its budget. A partial crawl is
    # a function of how much was already known when it ran, so serving it
    # later against a different `seen` set would be wrong.
    # The 4th element (claimed symbols) is cache-only: the caller contract
    # stays a 3-tuple, but a HIT needs these to rebuild the frontier.
    complete = novel_kept >= edge_limit or used >= MAX_ABSTRACTS_FOR_EXTRACTION
    if complete:
        cache.set(cache_key, (list(merged.values()), papers, meta, sorted(new_symbols)))
    else:
        logger.info(
            "Node %s: partial crawl (%d/%d new partners) not cached",
            protein.node_id, novel_kept, edge_limit,
        )
    logger.info(
        "Node crawl complete for '%s': %d new partners, %d redundant, "
        "%d edges from %d papers",
        protein.node_id, novel_kept, redundant_kept, len(merged), len(papers),
    )
    return result


def _clamp_edges_per_node(value) -> int:
    """Clamp a user-supplied per-node edge budget to a sane range.

    Single source of truth: both /api/graph and collect_interactions route
    through this, so a value cannot mean one thing at the edge and something
    else deeper in. Junk, zero and negatives fall back to the default rather
    than raising or silently becoming a 1-edge crawl.
    """
    try:
        n = int(value)
    except (TypeError, ValueError):
        return MAX_EDGES_PER_NODE
    if n <= 0:
        return MAX_EDGES_PER_NODE
    return max(EDGES_PER_NODE_MIN, min(n, EDGES_PER_NODE_MAX))


async def collect_interactions(
    protein: Protein,
    max_papers: int = MAX_PAPERS,
    depth: int = 2,
    edges_per_node: int = MAX_EDGES_PER_NODE,
) -> Tuple[List[Interaction], List, dict]:
    """Multi-level BFS crawl: collects interactions from the root protein AND
    recursively from all discovered neighbours up to the requested depth.

    Algorithm:
      1. Start with the root protein as the frontier (level 0).
      2. For each node in the frontier: search PubMed, fetch papers,
         extract interactions via LLM -- capped at `edges_per_node` NEW
         interactions for that node.
      3. Collect all discovered neighbour proteins.
      4. Those neighbours become the next frontier (level 1).
      5. Repeat until depth is reached, the frontier is empty, or the
         absolute LLM backstop is exhausted.
      6. Canonicalise all endpoints across all levels and merge.

    Every node in every level receives its own `edges_per_node` allowance, so
    recursion is uniform: depth=3 with edges_per_node=5 means every node
    contributes up to 5 of its own new edges, not 5 edges shared across the
    whole graph.

    Returns (all_interactions, all_papers, meta).
    """
    depth = max(1, min(int(depth or 1), 5))
    edge_limit = _clamp_edges_per_node(edges_per_node)
    logger.info(
        "collect_interactions START for protein='%s', max_papers=%d, depth=%d, "
        "edges_per_node=%d",
        protein.node_id, max_papers, depth, edge_limit,
    )

    llm_budget = {"count": 0, "max": MAX_LLM_CALLS}
    visited: Set[str] = {protein.node_id}
    frontier: List[Protein] = [protein]
    all_interactions: List[Interaction] = []
    all_papers: List[Paper] = []
    truncations: List[dict] = []
    # Declared OUTSIDE the level loop: it used to be re-initialised per level,
    # so meta for the root and every earlier level was silently discarded and
    # only the last level survived into the response.
    per_node_meta: Dict[str, dict] = {}

    for level in range(depth):
        if not frontier:
            logger.info("Frontier empty at level %d, stopping.", level)
            break
        if llm_budget["count"] >= llm_budget["max"]:
            truncations.append({"reason": "max_llm_calls", "level": level, "llm_calls_used": llm_budget["count"]})
            logger.warning("LLM budget exhausted before level %d", level)
            break

        next_frontier_proteins: List[Protein] = []
        level_interactions: List[Interaction] = []

        for node_protein in frontier:
            before = set(visited)
            interactions, papers, meta = await _crawl_node(
                node_protein, max_papers, edge_limit, llm_budget, visited
            )
            level_interactions.extend(interactions)
            all_papers.extend(papers)
            per_node_meta[node_protein.node_id] = meta

            # `visited` already contains everything this node claimed, so
            # whatever it added is exactly the new frontier material.
            # (A node served from cache re-claims its symbols inside
            # `_crawl_node`, so this works for HITs as well as fresh crawls.)
            for symbol in sorted(visited - before):
                resolved = await protein_service.get_protein_by_name(symbol)
                if resolved:
                    next_frontier_proteins.append(resolved)
                else:
                    # Claimed by the crawl but not resolvable to a Protein, so
                    # it can never be crawled itself. It still appears in the
                    # graph as a node with its edges; it just has no children.
                    logger.info(
                        "New symbol '%s' (from %s) is not resolvable; "
                        "it will appear as a leaf node only",
                        symbol, node_protein.node_id,
                    )

        all_interactions.extend(level_interactions)

        logger.info(
            "Level %d: %d interactions from %d nodes, %d new proteins for level %d",
            level, len(level_interactions), len(frontier), len(next_frontier_proteins), level + 1,
        )

        frontier = next_frontier_proteins

    # Canonicalise ALL endpoints across ALL levels at once.
    ambiguities: List[dict] = []
    if all_interactions:
        endpoints: Set[str] = set()
        for interaction in all_interactions:
            endpoints.add(interaction.source_protein)
            endpoints.add(interaction.target_protein)
        canonical = await protein_resolver.resolve_many(endpoints)

        def canonical_of(symbol: str) -> str:
            return canonical.get(
                protein_resolver.normalize(symbol), symbol.strip() if symbol else ""
            )

        rewritten = 0
        dropped = 0
        for interaction in all_interactions:
            new_source = canonical_of(interaction.source_protein)
            new_target = canonical_of(interaction.target_protein)
            if not new_source or not new_target or new_source == new_target:
                dropped += 1
                continue
            # Compare BEFORE overwriting. Assigning first and then comparing
            # new_source to interaction.source_protein compares a value with
            # itself, so this count was always 0 and the canonicalisation
            # summary was silently blank.
            if (new_source != interaction.source_protein
                    or new_target != interaction.target_protein):
                rewritten += 1
            interaction.source_protein = new_source
            interaction.target_protein = new_target

        # Re-merge edges that collapsed onto the same canonical pair.
        merged_map: dict = {}
        for interaction in all_interactions:
            if not interaction.source_protein or not interaction.target_protein:
                continue
            if interaction.source_protein == interaction.target_protein:
                continue
            key = interaction.key()
            if key in merged_map:
                merged_map[key].merge(interaction)
            else:
                merged_map[key] = interaction
        all_interactions = list(merged_map.values())

        logger.info(
            "Global canonicalisation: %d rewritten, %d dropped, %d final edges",
            rewritten, dropped, len(all_interactions),
        )
        # A symbol UniProt claims for two proteins is resolved (canonical name
        # wins) but reported, so a node label the user distrusts can be
        # explained rather than looked like a search error.
        # protein_resolver is a process-wide singleton, so `ambiguous`
        # accumulates across requests. Filter to this crawl's own symbols or
        # the report would keep growing and mention unrelated proteins.
        endpoints_norm = {protein_resolver.normalize(s) for s in endpoints}
        ambiguities = [
            a for a in protein_resolver.ambiguity_report()
            if protein_resolver.normalize(a["symbol"]) in endpoints_norm
        ]
        if ambiguities:
            logger.warning(
                "%d ambiguous symbol(s) resolved by canonical-name priority: %s",
                len(ambiguities),
                ", ".join(
                    f"{a['symbol']} -> {a['chosen']} (also maps to "
                    f"{', '.join(a['alternatives'])})"
                    for a in ambiguities[:5]
                ),
            )

    nodes_crawled = len(per_node_meta)
    capped_nodes = sum(
        1 for m in per_node_meta.values() if m.get("edges_dropped_by_cap", 0) > 0
    )
    total_new = sum(m.get("new_partners", 0) for m in per_node_meta.values())
    total_redundant = sum(
        m.get("redundant_edges", 0) for m in per_node_meta.values()
    )
    meta = {
        "max_papers": max_papers,
        "depth_requested": depth,
        "edges_per_node": edge_limit,
        "nodes_crawled": nodes_crawled,
        "nodes_hitting_edge_cap": capped_nodes,
        "new_partners_found": total_new,
        "redundant_edges": total_redundant,
        "levels_completed": depth if not truncations else truncations[-1].get("level", 0),
        "total_llm_calls": llm_budget["count"],
        "max_llm_calls": MAX_LLM_CALLS,
        "truncations": truncations,
        "per_node_meta": per_node_meta if all_interactions else {},
        "total_pmids": len(all_papers),
        "ambiguous_symbols": ambiguities,
    }

    logger.info(
        "collect_interactions complete for '%s': %d interactions across %d levels, %d LLM calls",
        protein.node_id, len(all_interactions), depth, llm_budget["count"],
    )
    return all_interactions, all_papers, meta


@bp.route("/api/search", methods=["POST"])
def search_protein():
    """Resolve a protein by name, gene symbol or UniProt accession."""
    query = (_json_body().get("query") or "").strip()
    logger.info("POST /api/search — query='%s'", query)
    if not query:
        logger.warning("Search rejected: empty query")
        return jsonify({"error": "Query is required"}), 400

    protein = run_async(protein_service.get_protein_by_name(query))
    if not protein:
        logger.warning("Could not resolve protein: '%s'", query)
        return (
            jsonify(
                {
                    "error": f"Could not resolve '{query}' to a protein",
                    "hint": "Try a gene symbol (TP53), a protein name, or a UniProt accession (P04637).",
                }
            ),
            404,
        )

    logger.info("Protein found: '%s' (UniProt=%s, gene=%s)", protein.name, protein.uniprot_id, protein.gene_name)
    return jsonify(
        {
            "uniprot_id": protein.uniprot_id,
            "name": protein.name,
            "gene_name": protein.gene_name,
            "description": protein.description,
            "search_terms": protein.search_terms(),
        }
    )


@bp.route("/api/protein/<uniprot_id>", methods=["GET"])
def get_protein(uniprot_id):
    """Detailed protein information.

    Real UniProt annotations are authoritative. LLM-generated text is only
    used when a real backend is configured, and never as an overwrite of
    curated data.
    """
    logger.info("GET /api/protein/<uniprot_id> — uniprot_id='%s'", uniprot_id)
    protein = run_async(protein_service.get_protein_by_id(uniprot_id))
    if not protein:
        logger.warning("Protein not found: '%s'", uniprot_id)
        return jsonify({"error": "Protein not found"}), 404

    payload = protein.to_dict()
    logger.info("Protein retrieved: '%s' (%s)", protein.name, protein.uniprot_id)

    if llm_service.is_configured:
        generated = run_async(llm_service.generate_protein_description(protein.name))
        if generated:
            payload["llm_summary"] = {
                key: value
                for key, value in generated.items()
                if value and not key.startswith("_")
            }
            logger.debug("LLM summary added for protein '%s'", protein.name)
    payload["llm_enabled"] = llm_service.is_configured
    return jsonify(payload)


@bp.route("/api/interactions", methods=["POST"])
def get_interactions():
    """Interactions for a protein, with the papers that support them."""
    body = _json_body()
    protein_id = (body.get("protein_id") or "").strip()
    depth = body.get("depth", 2)
    logger.info("POST /api/interactions — protein_id='%s', depth=%d", protein_id, depth)
    if not protein_id:
        logger.warning("Interactions request rejected: missing protein_id")
        return jsonify({"error": "Protein ID is required"}), 400

    protein = run_async(protein_service.get_protein_by_id(protein_id))
    if not protein:
        logger.warning("Protein not found for interactions: '%s'", protein_id)
        return jsonify({"error": "Protein not found"}), 404

    logger.info("Collecting interactions for protein '%s'...", protein_id)
    interactions, _papers, meta = run_async(
        collect_interactions(
            protein,
            depth=depth,
            edges_per_node=_clamp_edges_per_node(
                _json_body().get("edges_per_node")
            ),
        )
    )
    logger.info(
        "Interactions collected for '%s': %d interactions, %d papers",
        protein_id, len(interactions), meta.get("total_pmids", 0),
    )
    return jsonify(
        {
            "protein": protein.to_dict(),
            "interactions": [i.to_dict(include_context=True) for i in interactions],
            "total_interactions": len(interactions),
            **meta,
        }
    )


@bp.route("/api/connection-summary", methods=["POST"])
def get_connection_summary():
    """Generate an LLM summary of what a connection does and what the papers say about it."""
    body = _json_body()
    source_protein = (body.get("source_protein") or "").strip()
    target_protein = (body.get("target_protein") or "").strip()
    interaction_type = (body.get("interaction_type") or "").strip()
    paper_titles = body.get("paper_titles") or ""
    context = body.get("context")

    logger.info(
        "POST /api/connection-summary — %s - %s (%s)",
        source_protein, target_protein, interaction_type,
    )
    if not source_protein or not target_protein:
        logger.warning("Connection summary rejected: missing proteins")
        return jsonify({"error": "Source and target proteins are required"}), 400

    summary = run_async(
        llm_service.generate_connection_summary(
            source_protein, target_protein, interaction_type,
            paper_titles, context,
        )
    )
    logger.info("Connection summary generated for %s - %s", source_protein, target_protein)
    return jsonify({
        "source_protein": source_protein,
        "target_protein": target_protein,
        "interaction_type": interaction_type,
        "summary": summary,
    })


@bp.route("/api/papers/<pmid>", methods=["GET"])
def get_paper(pmid):
    """Full details for one paper."""
    logger.info("GET /api/papers/<pmid> — pmid='%s'", pmid)
    paper = run_async(pubmed_service.fetch_paper_details(pmid))
    if not paper:
        logger.warning("Paper not found: pmid='%s'", pmid)
        return jsonify({"error": "Paper not found"}), 404
    logger.info("Paper retrieved: pmid='%s', title='%s'", pmid, paper.title[:60])
    return jsonify(paper.to_dict())


@bp.route("/api/graph", methods=["POST"])
def get_graph():
    """Graph data for vis.js, centred on a protein."""
    body = _json_body()
    protein_id = (body.get("protein_id") or "").strip()
    depth = body.get("depth", 2)
    filters = body.get("filters")
    # Per-node edge budget, controlled by the UI slider. Clamped server-side:
    # the client value is a hint, not authority.
    edges_per_node = _clamp_edges_per_node(body.get("edges_per_node"))

    logger.info(
        "POST /api/graph — protein_id='%s', depth=%s, edges_per_node=%d, filters=%s",
        protein_id, depth, edges_per_node, filters,
    )
    if not protein_id:
        logger.warning("Graph request rejected: missing protein_id")
        return jsonify({"error": "Protein ID is required"}), 400

    protein = run_async(protein_service.get_protein_by_id(protein_id))
    if not protein:
        logger.warning("Protein not found for graph: '%s'", protein_id)
        return jsonify({"error": "Protein not found"}), 404

    logger.info("Collecting interactions for graph building...")
    interactions, papers, meta = run_async(
        collect_interactions(
            protein, depth=depth, edges_per_node=edges_per_node
        )
    )
    logger.info(
        "Building graph: %d interactions, %d papers, depth=%d",
        len(interactions), len(papers), depth,
    )
    graph_data = graph_service.build_graph(protein, interactions, depth, filters)

    graph_data["papers"] = [
        {"pmid": p.pmid, "title": p.title, "year": p.year, "url": p.url} for p in papers
    ]
    graph_data["meta"] = {**meta, **graph_data.get("stats", {})}
    graph_data["llm"] = llm_service.health()

    # Secondary, non-competing messages. `warning` above is a single banner
    # picked by priority, so anything that must not hide a truncation goes
    # here instead of competing for that slot.
    notices: List[str] = []
    for a in meta.get("ambiguous_symbols") or []:
        others = ", ".join(a.get("alternatives") or [])
        notices.append(
            f"UniProt gives '{a['symbol']}' to more than one protein. It was "
            f"matched to {a['chosen']} because that is its official gene "
            f"name, but it is also a recorded name for {others}. If you "
            f"expected {others}, the node label is the resolver's choice, not "
            f"a PubMed error."
        )
    if meta.get("redundant_edges"):
        notices.append(
            f"{meta['redundant_edges']} of the edges link proteins that were "
            f"already in the graph. They are kept as evidence but did not "
            f"count towards the {meta.get('edges_per_node')}-new-partners "
            f"budget, which only counts proteins not seen before."
        )
    if notices:
        graph_data["notices"] = notices

    if pubmed_service.last_error:
        graph_data["warning"] = (
            f"PubMed search failed, so this graph is incomplete or empty. "
            f"{pubmed_service.last_error}"
        )
        graph_data["severity"] = "error"
    elif not llm_service.is_configured:
        graph_data["warning"] = (
            "No LLM is configured, so no interactions were extracted. "
            "Set PROTEIN_LLM_API_KEY in .env and restart the backend. "
            "This app does not fabricate interactions."
        )
        graph_data["severity"] = "error"
    elif meta.get("truncations"):
        # Render the reason readably instead of interpolating a raw dict.
        parts = []
        for t in meta["truncations"]:
            if t.get("reason") == "max_llm_calls":
                parts.append(
                    f"the {meta.get('max_llm_calls')}-call safety ceiling was hit "
                    f"at level {t.get('level')}"
                )
            else:
                parts.append(f"{t.get('reason')} at level {t.get('level')}")
        graph_data["warning"] = (
            f"Crawl stopped early: {'; '.join(parts)}. "
            f"LLM calls used: {meta.get('total_llm_calls', 0)}"
            f"/{meta.get('max_llm_calls', 'unknown')}. "
            f"Ran {meta.get('nodes_crawled', 0)} protein(s) to depth "
            f"{meta.get('levels_completed', 0)} of {meta.get('depth_requested', '?')}. "
            f"The graph is incomplete."
        )
        graph_data["severity"] = "warning"
    elif meta.get("nodes_hitting_edge_cap"):
        # Not a failure -- the per-node cap working as designed. Surfaced so
        # the user knows the slider is the thing to raise for a denser graph.
        capped = meta["nodes_hitting_edge_cap"]
        crawled = meta.get("nodes_crawled", 0)
        graph_data["warning"] = (
            f"{capped} of {crawled} protein(s) hit the "
            f"{meta.get('edges_per_node')}-edge-per-node limit, so those nodes "
            f"were cut off mid-paper. Raise 'Edges per node' for a denser graph."
        )
        graph_data["severity"] = "info"
    elif not meta.get("per_node_meta"):
        graph_data["warning"] = (
            "PubMed returned no papers for this protein, so there is no "
            "evidence to extract from."
        )
        graph_data["severity"] = "warning"
    else:
        graph_data["severity"] = "ok"
    return jsonify(graph_data)


@bp.route("/api/health", methods=["GET"])
def health():
    health_data = {
        "status": "ok",
        "llm": llm_service.health(),
        "pubmed_email_configured": bool(
            pubmed_service.email and "example.com" not in pubmed_service.email
        ),
        "pubmed_api_key": bool(pubmed_service.api_key),
    }
    logger.info("Health check: %s", health_data)
    return jsonify(health_data)


@bp.route("/api/cache", methods=["DELETE"])
def clear_cache():
    logger.info("Cache clear requested via API")
    cache.clear()
    return jsonify({"status": "cleared"})


@bp.route("/api/validate/graph", methods=["POST"])
def validate_graph():
    """Definitive duplicate-protein check for a graph payload.

    Body: {"protein_id": "TP53", "depth": 2, "filters": null}

    Rebuilds the graph exactly as /api/graph would, then verifies that no two
    nodes denote the same protein. Identity is decided by UniProt accession,
    so "TP53" and "p53" are caught even though they are different strings.

    Returns 200 with {"ok": true} when unique, 409 when duplicates exist.
    """
    body = _json_body()
    protein_id = (body.get("protein_id") or "").strip()
    if not protein_id:
        return jsonify({"error": "Protein ID is required"}), 400

    depth = body.get("depth", 2)
    filters = body.get("filters")

    logger.info("Validating graph uniqueness for protein_id='%s'", protein_id)

    protein = run_async(protein_service.get_protein_by_id(protein_id))
    if not protein:
        return jsonify({"error": "Protein not found"}), 404

    interactions, _papers, _meta = run_async(
        collect_interactions(
            protein,
            depth=depth,
            edges_per_node=_clamp_edges_per_node(body.get("edges_per_node")),
        )
    )
    graph_data = graph_service.build_graph(protein, interactions, depth, filters)

    report = run_async(
        check_graph_protein_uniqueness(graph_data, protein_resolver)
    )
    report["protein_id"] = protein_id
    logger.info(
        "Graph uniqueness for '%s': ok=%s (%d nodes, %d distinct accessions)",
        protein_id, report["ok"], report["node_count"],
        report.get("distinct_accessions", -1),
    )

    return jsonify(report), (200 if report["ok"] else 409)
