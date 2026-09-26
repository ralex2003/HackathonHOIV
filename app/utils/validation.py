"""Definitive duplicate-protein check for a built graph.

Two different things are checked, because they fail differently:

1. `duplicate_ids` - the same node id appears twice. vis-network would raise
   "Cannot add item" on this, so it is always fatal.
2. `duplicate_proteins` - two DIFFERENT node ids denote the SAME protein
   (TP53 vs p53, or EGFR vs ERBB1). The graph renders fine and looks
   plausible, which is exactly why it needs an explicit check.

Usage:
    report = await check_graph_protein_uniqueness(graph, resolver)
    if not report["ok"]:
        ...  # report["duplicate_proteins"] says which nodes collided
"""

import logging
from typing import Dict, List, Optional

from app.services.protein_resolver import ProteinResolver

logger = logging.getLogger(__name__)


async def check_graph_protein_uniqueness(
    graph: Dict, resolver: Optional[ProteinResolver] = None
) -> Dict:
    """Verify every node is a distinct protein.

    Returns a report; never raises, so it can be called from a health endpoint
    or a test. `ok` is True only when both duplicate classes are empty.
    """
    nodes = graph.get("nodes") or []
    node_ids = [n.get("id") for n in nodes]

    report: Dict = {
        "ok": False,
        "node_count": len(nodes),
        "duplicate_ids": [],
        "duplicate_proteins": [],
        "unresolved_symbols": [],
        "checked_by": "uniprot_accession" if resolver else "string_identity",
    }

    # --- 1. exact id collisions -------------------------------------------
    seen_ids = set()
    for node_id in node_ids:
        if node_id in seen_ids and node_id not in report["duplicate_ids"]:
            report["duplicate_ids"].append(node_id)
        seen_ids.add(node_id)

    # Cheap structural check that needs no network.
    if not resolver:
        # Catches case and whitespace variants only ("P53" vs "p53",
        # "TP53 " vs "TP53"). It CANNOT catch alias-different spellings of one
        # protein, because "p53".upper() is "P53", a different string from
        # "TP53" - deciding those are the same protein requires UniProt.
        buckets: Dict[str, List[str]] = {}
        for node_id in node_ids:
            buckets.setdefault(str(node_id).strip().upper(), []).append(node_id)
        for key, members in buckets.items():
            if len(members) > 1:
                report["duplicate_proteins"].append(
                    {"canonical": key, "nodes": members, "basis": "case_insensitive"}
                )
        report["ok"] = not report["duplicate_ids"] and not report["duplicate_proteins"]
        report["limitation"] = (
            "No resolver supplied: only case/whitespace variants were checked. "
            "Alias-different spellings (TP53 vs p53, EGFR vs ERBB1) require a "
            "ProteinResolver to detect."
        )
        if not report["ok"]:
            logger.warning(
                "Duplicate node ids/variants detected: %s",
                report["duplicate_proteins"] or report["duplicate_ids"],
            )
        return report

    # --- 2. real protein identity via UniProt -----------------------------
    symbols = [n for n in node_ids if n]
    # Both resolver calls key by NORMALISED symbol, so normalise here too.
    norm = {n: resolver.normalize(n) for n in symbols}
    accessions = await resolver.resolve_accessions(symbols)
    canonical = await resolver.resolve_many(symbols)

    by_accession: Dict[str, List[str]] = {}
    for node_id in symbols:
        key = norm[node_id]
        accession = accessions.get(key)
        if accession:
            by_accession.setdefault(accession, []).append(node_id)
        else:
            report["unresolved_symbols"].append(node_id)

    for accession, members in sorted(by_accession.items()):
        if len(members) > 1:
            names = sorted({canonical.get(norm[m], m) for m in members})
            report["duplicate_proteins"].append(
                {
                    "accession": accession,
                    "nodes": sorted(members),
                    "canonical_names": names,
                    "basis": "uniprot_accession",
                }
            )

    if report["duplicate_proteins"]:
        logger.warning(
            "Duplicate proteins detected in graph: %s",
            [d["nodes"] for d in report["duplicate_proteins"]],
        )
    else:
        logger.info(
            "Uniqueness check passed: %d nodes, %d distinct UniProt accessions",
            len(nodes), len(by_accession),
        )

    report["distinct_accessions"] = len(by_accession)
    if report["unresolved_symbols"]:
        report["limitation"] = (
            f"{len(report['unresolved_symbols'])} node(s) could not be identified "
            f"in UniProt and were checked by string only: "
            f"{sorted(set(report['unresolved_symbols']))}"
        )
    report["ok"] = not report["duplicate_ids"] and not report["duplicate_proteins"]
    return report
