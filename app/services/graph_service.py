from typing import Dict, List, Set
from app.models.protein import Protein
from app.models.interaction import Interaction

class GraphService:
    def __init__(self):
        self.visited_proteins: Set[str] = set()
    
    def build_graph(self, central_protein: Protein, interactions: List[Interaction], 
                    depth: int = 2, active_filters: List[str] = None) -> Dict:
        """
        Build graph data structure for Vis.js visualization.
        
        Args:
            central_protein: The starting protein
            interactions: List of all interactions
            depth: Maximum depth of the graph
            active_filters: List of interaction types to include (None = all)
        
        Returns:
            Dictionary with nodes and edges for Vis.js
        """
        if active_filters is None:
            active_filters = list(Interaction.TYPES.keys())
        
        nodes = {}
        edges = []
        
        # Add central node
        nodes[central_protein.uniprot_id] = {
            'id': central_protein.uniprot_id,
            'label': central_protein.name,
            'title': central_protein.description or central_protein.name,
            'level': 0,
            'color': '#FF6B6B',
            'size': 30
        }
        
        # Build interaction network
        self._add_interactions_to_graph(
            central_protein.uniprot_id, 
            interactions, 
            nodes, 
            edges, 
            current_depth=0, 
            max_depth=depth,
            active_filters=active_filters
        )
        
        return {
            'nodes': list(nodes.values()),
            'edges': edges
        }
    
    def _add_interactions_to_graph(self, protein_id: str, interactions: List[Interaction],
                                   nodes: Dict, edges: List, current_depth: int,
                                   max_depth: int, active_filters: List[str]):
        """Recursively add interactions to graph"""
        if current_depth >= max_depth:
            return
        
        # Find interactions involving this protein
        related_interactions = [
            i for i in interactions 
            if i.source_protein == protein_id or i.target_protein == protein_id
            and i.interaction_type in active_filters
        ]
        
        for interaction in related_interactions:
            # Determine the other protein
            other_protein = (interaction.target_protein if interaction.source_protein == protein_id 
                           else interaction.source_protein)
            
            # Add node if not exists
            if other_protein not in nodes:
                nodes[other_protein] = {
                    'id': other_protein,
                    'label': other_protein,
                    'title': other_protein,
                    'level': current_depth + 1,
                    'color': '#4ECDC4',
                    'size': 20
                }
            
            # Add edge
            edge_exists = any(
                e['from'] == protein_id and e['to'] == other_protein 
                for e in edges
            )
            
            if not edge_exists:
                edges.append({
                    'from': protein_id,
                    'to': other_protein,
                    'label': Interaction.TYPES.get(interaction.interaction_type, interaction.interaction_type),
                    'title': f"{Interaction.TYPES.get(interaction.interaction_type, interaction.interaction_type)} ({len(interaction.papers)} papers)",
                    'interaction_type': interaction.interaction_type,
                    'paper_count': len(interaction.papers),
                    'papers': [p.pmid for p in interaction.papers]
                })
            
            # Recursively add interactions for the other protein
            self._add_interactions_to_graph(
                other_protein, 
                interactions, 
                nodes, 
                edges, 
                current_depth + 1, 
                max_depth,
                active_filters
            )
