from typing import Dict, List, Optional
import random

class MockLLMService:
    """
    Mock LLM service for protein interaction extraction.
    This is a placeholder implementation that will be replaced with Groq/Kimi.
    """
    
    async def extract_interactions(self, paper_text: str, protein_name: str) -> Dict:
        """
        Extract protein-protein interactions from paper text.
        Returns structured JSON with interactions and protein info.
        """
        # Mock response - in real implementation, this would call an LLM
        mock_proteins = ['AKT1', 'MAPK1', 'EGFR', 'STAT3', 'NFkB', 'CASP3', 'BCL2', 'MDM2']
        interaction_types = ['physical_binding', 'regulatory', 'complex_formation', 'genetic']
        
        # Generate 1-3 random interactions
        num_interactions = random.randint(1, 3)
        interactions = []
        
        for _ in range(num_interactions):
            target = random.choice(mock_proteins)
            if target != protein_name:
                interactions.append({
                    'source_protein': protein_name,
                    'target_protein': target,
                    'interaction_type': random.choice(interaction_types),
                    'context': f"Interaction between {protein_name} and {target} observed in experimental conditions."
                })
        
        return {
            'interactions': interactions,
            'protein_info': {
                'function': f"{protein_name} is involved in key cellular processes including signal transduction and cell cycle regulation.",
                'pathways': f"{protein_name} participates in MAPK signaling, apoptosis, and cell growth pathways.",
                'localization': f"{protein_name} is primarily localized in the cytoplasm and nucleus."
            }
        }
    
    async def generate_protein_description(self, protein_name: str) -> Dict:
        """Generate detailed protein description using LLM"""
        # Mock response
        return {
            'function': f"{protein_name} functions as a key regulator in cellular processes, including proliferation, differentiation, and apoptosis.",
            'pathways': f"{protein_name} is involved in multiple signaling pathways such as PI3K-Akt, MAPK, and p53 pathways.",
            'localization': f"{protein_name} shows dynamic localization between cytoplasm and nucleus depending on cellular state."
        }
