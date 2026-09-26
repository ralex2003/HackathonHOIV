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

MAX_PAPERS = 3
MAX_ABSTRACTS_FOR_EXTRACTION = int(
    os.environ.get("PROTEIN_LLM_MAX_ABSTRACTS", "3")
)
# Total cap on LLM calls across ALL levels of the crawl.
# At 3 abstracts per node and 2 levels, that's ~9 calls max for depth=2.
MAX_LLM_CALLS = int(os.environ.get("PROTEIN_LLM_MAX_TOTAL_CALLS", "30"))

def run_async(coro):
    return asyncio.run(coro)


def _json_body() -> dict:
    return request.get_json(silent=True) or {}


async def _crawl_node(
    protein: Protein,
    max_papers: int,
    llm_budget_ref: dict,
) -> Tuple[List[Interaction], List[Paper], dict]:
    """Fetch papers for one protein and extract interactions from their abstracts.

    Returns (interactions, papers, meta) for this single node.
    Respects llm_budget_ref and returns empty results if the budget is exhausted.
    """
    terms = protein.search_terms()
    if not terms:
        return [], [], {"query": None, "total_matches": 0, "skipped": "no_search_terms"}

    cache_key = f"node:{protein.uniprot_id}:{max_papers}:{llm_service.is_configured}"
    cached = cache.get(cache_key)
    if cached is not None:
        logger.info("Node cache HIT for %s", protein.node_id)
        return cached

    logger.info("Node crawl: protein='%s', searching PubMed...", protein.node_id)
    query = pubmed_service.build_query(terms)
    pmids = await pubmed_service.search_papers(query, max_results=max_papers)
    papers = await pubmed_service.fetch_multiple_papers(pmids)

    symbol = protein.node_id
    merged: dict = {}
    used = 0
    sources: dict = {}

    for paper in papers:
        if not paper.abstract or used >= MAX_ABSTRACTS_FOR_EXTRACTION:
            continue
        if llm_budget_ref["count"] >= llm_budget_ref["max"]:
            logger.warning("LLM budget exhausted at node %s", protein.node_id)
            break

        used += 1
        llm_budget_ref["count"] += 1
        logger.debug(
            "Extracting interactions from PMID=%s (%d/%d max_llm=%d)",
            paper.pmid, llm_budget_ref["count"], llm_budget_ref["max"], llm_budget_ref["max"],
        )
        extracted = await llm_service.extract_interactions(paper.abstract, symbol)
        source = extracted.get("source", "unknown")
        sources[source] = sources.get(source, 0) + 1
        for item in extracted.get("interactions", []) or []:
            src = item.get("source_protein")
            tgt = item.get("target_protein")
            if not src or not tgt or src == tgt:
                continue
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
                merged[key].merge(interaction)
            else:
                merged[key] = interaction

    meta = {
        "query": query,
        "total_pmids": len(pmids),
        "papers_fetched": len(papers),
        "papers_with_abstract": used,
        "extraction_sources": sources,
        "llm_calls_used": llm_budget_ref["count"],
    }
    result = (list(merged.values()), papers, meta)
    cache.set(cache_key, result)
    logger.info(
        "Node crawl complete for '%s': %d interactions from %d papers",
        protein.node_id, len(merged), len(papers),
    )
    return result


async def collect_interactions(
    protein: Protein, max_papers: int = MAX_PAPERS, depth: int = 2
) -> Tuple[List[Interaction], List, dict]:
    """Multi-level BFS crawl: collects interactions from the root protein AND
    recursively from all discovered neighbours up to the requested depth.

    Algorithm:
      1. Start with the root protein as the frontier (level 0).
      2. For each node in the frontier: search PubMed, fetch papers,
         extract interactions via LLM.
      3. Collect all discovered neighbour proteins.
      4. Those neighbours become the next frontier (level 1).
      5. Repeat until depth is reached, the frontier is empty, or the
         LLM budget is exhausted.
      6. Canonicalise all endpoints across all levels and merge.

    Returns (all_interactions, all_papers, meta).
    """
    depth = max(1, min(int(depth or 1), 5))
    logger.info(
        "collect_interactions START for protein='%s', max_papers=%d, depth=%d",
        protein.node_id, max_papers, depth,
    )

    llm_budget = {"count": 0, "max": MAX_LLM_CALLS}
    visited: Set[str] = {protein.node_id}
    frontier: List[Protein] = [protein]
    all_interactions: List[Interaction] = []
    all_papers: List[Paper] = []
    truncations: List[dict] = []

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
        per_node_meta: Dict[str, dict] = {}

        for node_protein in frontier:
            interactions, papers, meta = await _crawl_node(
                node_protein, max_papers, llm_budget
            )
            level_interactions.extend(interactions)
            all_papers.extend(papers)
            per_node_meta[node_protein.node_id] = meta

            # Discover new proteins at this level for the next frontier.
            for interaction in interactions:
                for endpoint in (interaction.source_protein, interaction.target_protein):
                    if endpoint not in visited:
                        visited.add(endpoint)
                        resolved = await protein_service.get_protein_by_name(endpoint)
                        if resolved:
                            next_frontier_proteins.append(resolved)

        all_interactions.extend(level_interactions)

        logger.info(
            "Level %d: %d interactions from %d nodes, %d new proteins for level %d",
            level, len(level_interactions), len(frontier), len(next_frontier_proteins), level + 1,
        )

        frontier = next_frontier_proteins

    # Canonicalise ALL endpoints across ALL levels at once.
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
            interaction.source_protein = new_source
            interaction.target_protein = new_target
            if new_source != interaction.source_protein or new_target != interaction.target_protein:
                rewritten += 1

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

    meta = {
        "max_papers": max_papers,
        "depth_requested": depth,
        "levels_completed": depth if not truncations else truncations[-1].get("level", 0),
        "total_llm_calls": llm_budget["count"],
        "max_llm_calls": MAX_LLM_CALLS,
        "truncations": truncations,
        "per_node_meta": per_node_meta if all_interactions else {},
        "total_pmids": len(all_papers),
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
    interactions, _papers, meta = run_async(collect_interactions(protein, depth=depth))
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

    logger.info(
        "POST /api/graph — protein_id='%s', depth=%d, filters=%s",
        protein_id, depth, filters,
    )
    if not protein_id:
        logger.warning("Graph request rejected: missing protein_id")
        return jsonify({"error": "Protein ID is required"}), 400

    protein = run_async(protein_service.get_protein_by_id(protein_id))
    if not protein:
        logger.warning("Protein not found for graph: '%s'", protein_id)
        return jsonify({"error": "Protein not found"}), 404

    logger.info("Collecting interactions for graph building...")
    interactions, papers, meta = run_async(collect_interactions(protein, depth=depth))
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
        trunc = meta["truncations"][0]
        graph_data["warning"] = (
            f"Crawl was truncated: {trunc}. "
            f"Total LLM calls used: {meta.get('total_llm_calls', 0)}/{meta.get('max_llm_calls', 'unknown')}. "
            f"Graph may be incomplete."
        )
        graph_data["severity"] = "warning"
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

    interactions, _papers, _meta = run_async(collect_interactions(protein, depth=depth))
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
