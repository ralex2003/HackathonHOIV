import asyncio
import os
import logging
from typing import List, Tuple

from flask import Blueprint, jsonify, request

from app.models.interaction import Interaction
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

MAX_PAPERS = 50

# How many abstracts to run through extraction per request. This is the main
# cost lever: one LLM call per abstract. Groq's free tier allows roughly
# 8000 tokens/minute, so a large value means minutes of waiting and 429s.
MAX_ABSTRACTS_FOR_EXTRACTION = int(
    os.environ.get("PROTEIN_LLM_MAX_ABSTRACTS", "20")
)


def run_async(coro):
    """Run a coroutine on a dedicated loop and always close it.

    The previous handler code built a new loop per request and never closed
    it, leaking a file descriptor per request.
    """
    return asyncio.run(coro)


def _json_body() -> dict:
    return request.get_json(silent=True) or {}


async def collect_interactions(
    protein: Protein, max_papers: int = MAX_PAPERS
) -> Tuple[List[Interaction], List, dict]:
    """Gather papers for a protein and extract interactions from them.

    Interactions reported by more than one paper are merged into a single edge
    so that `paper_count` reflects real supporting evidence. Previously each
    paper produced its own Interaction, so every edge reported exactly 1 paper
    no matter how much literature backed it.

    The merged result is cached per protein because extraction is the expensive
    stage: with a real LLM backend this is one model call per abstract, and
    filter/depth changes must not re-pay for it.
    """
    terms = protein.search_terms()
    logger.info(
        "collect_interactions started for protein='%s', max_papers=%d, search_terms=%s",
        protein.node_id, max_papers, terms,
    )
    if not terms:
        logger.warning("No search terms for protein '%s'", protein.node_id)
        return [], [], {"query": None, "total_matches": 0}

    cache_key = f"interactions:{protein.uniprot_id}:{max_papers}:{llm_service.is_configured}"
    cached = cache.get(cache_key)
    if cached is not None:
        logger.info("Cache HIT for interactions: %s", cache_key)
        interactions, papers, meta = cached
        return list(interactions), list(papers), dict(meta)

    logger.info("Cache MISS for interactions, fetching from PubMed...")
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
        used += 1
        logger.debug(
            "Extracting interactions from paper PMID=%s (%d/%d)",
            paper.pmid, used, MAX_ABSTRACTS_FOR_EXTRACTION,
        )
        extracted = await llm_service.extract_interactions(paper.abstract, symbol)
        source = extracted.get("source", "unknown")
        sources[source] = sources.get(source, 0) + 1
        for item in extracted.get("interactions", []) or []:
            source = item.get("source_protein")
            target = item.get("target_protein")
            if not source or not target or source == target:
                continue
            interaction = Interaction(
                source_protein=source,
                target_protein=target,
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

    # Canonicalise every endpoint to a single UniProt-approved symbol BEFORE
    # the graph is built, so one protein can only ever become one node. Without
    # this, "p53" from one abstract and "TP53" from another produce two nodes
    # for the same protein, joined by nothing.
    endpoints = set()
    for interaction in merged.values():
        endpoints.add(interaction.source_protein)
        endpoints.add(interaction.target_protein)
    canonical = await protein_resolver.resolve_many(endpoints)

    rewritten = 0
    dropped = 0
    collapsed = 0
    remapped: dict = {}

    def canonical_of(symbol: str) -> str:
        # resolve_many() keys by NORMALISED symbol, so the lookup key must be
        # normalised too. Looking up the raw string silently missed every
        # lower-case endpoint, which is exactly the alias case being fixed.
        return canonical.get(
            protein_resolver.normalize(symbol), symbol.strip() if symbol else ""
        )

    for interaction in merged.values():
        new_source = canonical_of(interaction.source_protein)
        new_target = canonical_of(interaction.target_protein)
        if new_source != interaction.source_protein or new_target != interaction.target_protein:
            rewritten += 1
            remapped[interaction.key()] = None
        if not new_source or not new_target or new_source == new_target:
            # The two endpoints turned out to be the same protein.
            dropped += 1
            continue
        interaction.source_protein = new_source
        interaction.target_protein = new_target

    # Re-key and re-merge, so edges that collapsed onto the same canonical pair
    # are combined rather than silently overwriting each other.
    if rewritten or dropped:
        rebuilt: dict = {}
        for interaction in merged.values():
            if not interaction.source_protein or not interaction.target_protein:
                continue
            if interaction.source_protein == interaction.target_protein:
                continue
            key = interaction.key()
            if key in rebuilt:
                rebuilt[key].merge(interaction)
                collapsed += 1
            else:
                rebuilt[key] = interaction
        merged = rebuilt
        logger.info(
            "Symbol canonicalisation: %d endpoint(s) rewritten, %d self-interaction(s) "
            "dropped, %d edge(s) merged",
            rewritten, dropped, collapsed,
        )

    meta = {
        "query": query,
        "total_pmids": len(pmids),
        "papers_fetched": len(papers),
        "papers_with_abstract": used,
        "abstracts_attempted_max": MAX_ABSTRACTS_FOR_EXTRACTION,
        "search_terms": terms,
        "extraction_sources": sources,
        "llm_status": llm_service.status,
        "symbols_canonicalised": rewritten,
        "self_interactions_dropped": dropped,
        "edges_merged_after_canonicalisation": collapsed,
    }
    result = (list(merged.values()), papers, meta)
    cache.set(cache_key, result)
    logger.info(
        "collect_interactions complete for '%s': %d interactions from %d papers",
        protein.node_id, len(merged), len(papers),
    )
    return result


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
    logger.info("POST /api/interactions — protein_id='%s'", protein_id)
    if not protein_id:
        logger.warning("Interactions request rejected: missing protein_id")
        return jsonify({"error": "Protein ID is required"}), 400

    protein = run_async(protein_service.get_protein_by_id(protein_id))
    if not protein:
        logger.warning("Protein not found for interactions: '%s'", protein_id)
        return jsonify({"error": "Protein not found"}), 404

    logger.info("Collecting interactions for protein '%s'...", protein_id)
    interactions, _papers, meta = run_async(collect_interactions(protein))
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
    interactions, papers, meta = run_async(collect_interactions(protein))
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

    sources = meta.get("extraction_sources", {})
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
    elif not meta.get("total_pmids"):
        graph_data["warning"] = (
            "PubMed returned no papers for this protein, so there is no "
            "evidence to extract from."
        )
        graph_data["severity"] = "warning"
    elif sources.get("error"):
        graph_data["warning"] = (
            f"LLM calls failed for {sources['error']} of "
            f"{meta.get('papers_with_abstract', 0)} abstracts, so those papers "
            f"contributed no edges. Last error: "
            f"{llm_service.health()['stats'].get('last_error')}"
        )
        graph_data["severity"] = "error"
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

    interactions, _papers, _meta = run_async(collect_interactions(protein))
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
