import asyncio
from Bio import Entrez
from typing import List, Dict, Optional
from app.models.paper import Paper
from app.utils.rate_limiter import AsyncRateLimiter
from app.utils.cache import cache

class PubMedService:
    def __init__(self, email: str = "your_email@example.com"):
        Entrez.email = email
        self.rate_limiter = AsyncRateLimiter(rate_limit=3)
    
    async def search_papers(self, query: str, max_results: int = 50) -> List[str]:
        """Search PubMed and return list of PMIDs"""
        cache_key = f"search:{query}:{max_results}"
        cached = cache.get(cache_key)
        if cached:
            return cached
        
        try:
            async with self.rate_limiter:
                # Use MeSH terms and keywords for better protein interaction search
                mesh_query = f'{query}[Mesh] OR {query}[Title/Abstract] AND ("protein-protein interaction"[Mesh] OR "binding sites"[Mesh] OR "protein binding"[Mesh])'
                handle = Entrez.esearch(db="pubmed", term=mesh_query, retmax=max_results)
                record = Entrez.read(handle)
                handle.close()
                
                pmids = record.get("IdList", [])
                cache.set(cache_key, pmids)
                return pmids
        except Exception as e:
            print(f"Error searching PubMed: {e}")
            return []
    
    async def fetch_paper_details(self, pmid: str) -> Optional[Paper]:
        """Fetch detailed information for a specific paper"""
        cache_key = f"paper:{pmid}"
        cached = cache.get(cache_key)
        if cached:
            return cached
        
        try:
            async with self.rate_limiter:
                handle = Entrez.efetch(db="pubmed", id=pmid, rettype="medline", retmode="text")
                record = handle.read()
                handle.close()
                
                # Parse the MEDLINE format
                paper = self._parse_medline(record, pmid)
                if paper:
                    cache.set(cache_key, paper)
                return paper
        except Exception as e:
            print(f"Error fetching paper {pmid}: {e}")
            return None
    
    def _parse_medline(self, medline_text: str, pmid: str) -> Optional[Paper]:
        """Parse MEDLINE format into Paper object"""
        lines = medline_text.split('\n')
        data = {}
        
        for line in lines:
            if line.strip():
                tag = line[:4].strip()
                content = line[6:].strip()
                
                if tag == 'TI':
                    data['title'] = content
                elif tag == 'AU':
                    data['authors'] = content
                elif tag == 'DP':
                    data['year'] = content.split()[0] if content else ''
                elif tag == 'AB':
                    data['abstract'] = content
        
        if 'title' not in data:
            return None
        
        return Paper(
            pmid=pmid,
            title=data.get('title', ''),
            authors=data.get('authors', ''),
            year=data.get('year', ''),
            abstract=data.get('abstract')
        )
    
    async def fetch_multiple_papers(self, pmids: List[str]) -> List[Paper]:
        """Fetch details for multiple papers concurrently"""
        tasks = [self.fetch_paper_details(pmid) for pmid in pmids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        papers = []
        for result in results:
            if isinstance(result, Paper):
                papers.append(result)
        
        return papers
