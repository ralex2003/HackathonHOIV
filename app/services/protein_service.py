from Bio import SeqIO, ExPASy
from typing import Optional, Dict
from app.models.protein import Protein
from app.utils.cache import cache

class ProteinService:
    def __init__(self):
        self.common_names_cache: Dict[str, str] = {}
    
    async def get_protein_by_name(self, name: str) -> Optional[Protein]:
        """Get protein information by common name"""
        # First try to find UniProt ID from common name
        uniprot_id = await self._name_to_uniprot_id(name)
        if not uniprot_id:
            return None
        
        return await self.get_protein_by_id(uniprot_id)
    
    async def get_protein_by_id(self, uniprot_id: str) -> Optional[Protein]:
        """Get protein information by UniProt ID"""
        cache_key = f"protein:{uniprot_id}"
        cached = cache.get(cache_key)
        if cached:
            return cached
        
        try:
            handle = ExPASy.get_sprot_raw(uniprot_id)
            record = SeqIO.read(handle, "swiss")
            handle.close()
            
            protein = Protein(
                uniprot_id=uniprot_id,
                name=record.name if hasattr(record, 'name') else uniprot_id,
                description=record.description if hasattr(record, 'description') else '',
                function=self._extract_function(record),
                pathways=self._extract_pathways(record),
                localization=self._extract_localization(record)
            )
            
            cache.set(cache_key, protein)
            return protein
        except Exception as e:
            print(f"Error fetching protein {uniprot_id}: {e}")
            # Return a basic protein object even if fetch fails
            return Protein(
                uniprot_id=uniprot_id,
                name=uniprot_id,
                description=f"Protein {uniprot_id}"
            )
    
    async def _name_to_uniprot_id(self, name: str) -> Optional[str]:
        """Convert common protein name to UniProt ID"""
        # For now, implement a simple mapping for common proteins
        # In production, you'd use UniProt's mapping API
        common_mappings = {
            'p53': 'P04637',
            'tp53': 'P04637',
            'insulin': 'P01308',
            'hemoglobin': 'P68871',
            'myoglobin': 'P02144',
            'actin': 'P60709',
            'tubulin': 'Q9H4B4',
        }
        
        name_lower = name.lower()
        if name_lower in common_mappings:
            return common_mappings[name_lower]
        
        # If it looks like a UniProt ID, return it directly
        if len(name) == 6 and name[0].isupper() and name[1:].isalnum():
            return name.upper()
        
        return None
    
    def _extract_function(self, record) -> str:
        """Extract protein function from SwissProt record"""
        try:
            comments = record.annotations.get('comment', '')
            if 'FUNCTION:' in comments:
                func_start = comments.index('FUNCTION:') + 9
                func_end = comments.find('.', func_start)
                if func_end != -1:
                    return comments[func_start:func_end].strip()
        except:
            pass
        return "Function information not available"
    
    def _extract_pathways(self, record) -> str:
        """Extract pathway information from SwissProt record"""
        try:
            comments = record.annotations.get('comment', '')
            if 'PATHWAY:' in comments:
                path_start = comments.index('PATHWAY:') + 8
                path_end = comments.find('.', path_start)
                if path_end != -1:
                    return comments[path_start:path_end].strip()
        except:
            pass
        return "Pathway information not available"
    
    def _extract_localization(self, record) -> str:
        """Extract subcellular localization from SwissProt record"""
        try:
            comments = record.annotations.get('comment', '')
            if 'SUBCELLULAR LOCATION:' in comments:
                loc_start = comments.index('SUBCELLULAR LOCATION:') + 20
                loc_end = comments.find('.', loc_start)
                if loc_end != -1:
                    return comments[loc_start:loc_end].strip()
        except:
            pass
        return "Localization information not available"
