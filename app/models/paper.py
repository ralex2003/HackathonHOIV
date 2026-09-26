from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Paper:
    pmid: str
    title: str
    authors: str
    year: str
    abstract: Optional[str] = None
    url: str = None
    author_list: List[str] = field(default_factory=list)
    journal: Optional[str] = None

    def __post_init__(self):
        if self.url is None:
            self.url = f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/"
        if not self.author_list and self.authors: 
            self.author_list = [a.strip() for a in self.authors.split(",") if a.strip()]

    def to_dict(self, include_abstract: bool = True) -> dict:
        data = {
            "pmid": self.pmid,
            "title": self.title,
            "authors": self.authors,
            "author_list": self.author_list,
            "year": self.year,
            "journal": self.journal,
            "url": self.url,
        }
        if include_abstract:
            data["abstract"] = self.abstract
        return data
