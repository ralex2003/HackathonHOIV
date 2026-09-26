from dataclasses import dataclass
from typing import Optional

@dataclass
class Paper:
    pmid: str
    title: str
    authors: str
    year: str
    abstract: Optional[str] = None
    url: str = None
    
    def __post_init__(self):
        if self.url is None:
            self.url = f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/"
