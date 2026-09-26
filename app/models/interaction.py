from dataclasses import dataclass
from typing import List, Optional

@dataclass
class Interaction:
    source_protein: str
    target_protein: str
    interaction_type: str  # physical_binding, regulatory, complex_formation, genetic
    papers: List  # List[Paper] objects
    context: Optional[str] = None
    
    # Interaction types for categorization
    TYPES = {
        'physical_binding': 'Physical Binding',
        'regulatory': 'Regulatory',
        'complex_formation': 'Complex Formation',
        'genetic': 'Genetic'
    }
