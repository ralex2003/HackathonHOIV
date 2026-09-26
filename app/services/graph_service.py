import logging
from collections import deque
from typing import Dict, List, Optional, Set

from app.models.interaction import Interaction
from app.models.protein import Protein

logger = logging.getLogger(__name__)

# Node styling by graph level.
LEVEL_COLORS = ["#FF6B6B", "#4ECDC4", "#F7DC6F", "#BB8FCE", "#85C1E9", "#F0B27A"]
LEVEL_SIZES = [30, 20, 16, 14, 12, 11]


class GraphService:
    def __init__(self):
        # Kept for backwards compatibility; traversal state is per-call now so
        # that concurrent requests cannot contaminate each other.
        self.visited_proteins: Set[str] = set()

    def build_graph(
        self,
        central_protein: Protein,
        interactions: List[Interaction],
        depth: int = 2,
        active_filters: Optional[List[str]] = None,
    ) -> Dict:
        """Build a vis.js-compatible graph, breadth-first from the centre.

        Args:
            central_protein: the protein the graph is centred on.
            interactions: interaction edges extracted from the literature.
            depth: how many hops from the centre to include.
            active_filters: interaction types to include (None = all types).

        Returns:
            {"nodes": [...], "edges": [...], "stats": {...}}
        """
        logger.info(
            "Building interaction graph: central_protein='%s', depth=%d, interactions=%d",
            central_protein.node_id, depth, len(interactions),
        )
        if active_filters is None:
            active_filters = list(Interaction.TYPES.keys())
            logger.debug("No filters specified, using all interaction types: %s", active_filters)
        else:
            active_filters = [f for f in active_filters if f in Interaction.TYPES]
            if not active_filters:
                active_filters = list(Interaction.TYPES.keys())
                logger.warning("All filters removed, defaulting to all interaction types")
            else:
                logger.info("Active filters: %s", active_filters)

        depth = max(1, min(int(depth or 1), 5))

        centre_id = central_protein.node_id
        nodes: Dict[str, dict] = {
            centre_id: {
                "id": centre_id,
                "label": central_protein.name or centre_id,
                "title": self._tooltip(central_protein),
                "level": 0,
                "color": LEVEL_COLORS[0],
                "size": LEVEL_SIZES[0],
                "uniprot_id": central_protein.uniprot_id,
                "gene_name": central_protein.gene_name,
                "description": central_protein.description,
                "is_central": True,
            }
        }

        # Index edges by endpoint so lookups during BFS are O(1) instead of
        # rescanning the whole list for every node (the old recursion was
        # O(nodes * edges) and could revisit nodes indefinitely).
        adjacency: Dict[str, List[Interaction]] = {}
        allowed = 0
        filtered_out = 0
        for interaction in interactions:
            if interaction.interaction_type not in active_filters:
                filtered_out += 1
                continue
            allowed += 1
            adjacency.setdefault(interaction.source_protein, []).append(interaction)
            adjacency.setdefault(interaction.target_protein, []).append(interaction)

        logger.info(
            "Graph indexing: %d interactions passed filter, %d filtered out",
            allowed, filtered_out,
        )

        edges: List[dict] = []
        seen_edges: Set[tuple] = set()
        visited: Set[str] = {centre_id}
        queue: deque = deque([(centre_id, 0)])

        logger.debug("Starting BFS from node '%s' with depth=%d", centre_id, depth)

        while queue:
            current_id, level = queue.popleft()
            if level >= depth:
                continue

            for interaction in adjacency.get(current_id, []):
                source = interaction.source_protein
                target = interaction.target_protein
                other = target if source == current_id else source
                if not other or other == current_id:
                    continue

                key = interaction.key()
                if key not in seen_edges:
                    seen_edges.add(key)
                    edges.append(self._build_edge(interaction, source, target, level, level + 1))

                if other in visited:
                    continue

                visited.add(other)
                nodes[other] = {
                    "id": other,
                    "label": other,
                    "title": self._neighbour_tooltip(other, interaction),
                    "level": level + 1,
                    "color": LEVEL_COLORS[min(level + 1, len(LEVEL_COLORS) - 1)],
                    "size": LEVEL_SIZES[min(level + 1, len(LEVEL_SIZES) - 1)],
                    "is_central": False,
                }
                queue.append((other, level + 1))

        result = {
            "nodes": list(nodes.values()),
            "edges": edges,
            "stats": {
                "central_protein": centre_id,
                "uniprot_id": central_protein.uniprot_id,
                "depth": depth,
                "node_count": len(nodes),
                "edge_count": len(edges),
                "interactions_considered": len(interactions),
                "interactions_after_filter": allowed,
                "active_filters": active_filters,
            },
        }

        logger.info(
            "Graph built: %d nodes, %d edges, depth=%d",
            len(nodes), len(edges), depth,
        )
        return result

    def _build_edge(self, interaction: Interaction, source: str, target: str,
                    source_level: int = 0, target_level: int = 1) -> dict:
        label = interaction.type_label
        pmids = [p.pmid for p in interaction.papers]
        level_skipping = abs(target_level - source_level) > 1
        context = interaction.context[:400] if interaction.context else None
        contexts = [interaction.context] if interaction.context else []
        return {
            "from": source,
            "to": target,
            "id": "|".join(interaction.key()),
            "label": label,
            "title": f"{label} ({len(pmids)} paper{'s' if len(pmids) != 1 else ''})",
            "interaction_type": interaction.interaction_type,
            "paper_count": len(pmids),
            "papers": pmids,
            "level_skipping": level_skipping,
            "source_level": source_level,
            "target_level": target_level,
            "context": context,
            "contexts": contexts,
        }

    @staticmethod
    def _tooltip(protein: Protein) -> str:
        parts = [f"<b>{protein.name}</b>"]
        if protein.description:
            parts.append(protein.description)
        if protein.organism:
            parts.append(f"Organism: {protein.organism}")
        parts.append(f"UniProt: {protein.uniprot_id}")
        return "<br>".join(parts)

    @staticmethod
    def _neighbour_tooltip(node_id: str, interaction: Interaction) -> str:
        parts = [f"<b>{node_id}</b>"]
        if interaction.context:
            parts.append(interaction.context[:300])
        if interaction.papers:
            first = interaction.papers[0]
            parts.append(f"Source: {first.title[:160]}")
        parts.append("Click for details")
        return "<br>".join(parts)
