from dataclasses import dataclass
from typing import Optional

@dataclass
class Protein:
    uniprot_id: str
    name: str
    function: Optional[str] = None
    pathways: Optional[str] = None
    localization: Optional[str] = None
    description: Optional[str] = None
