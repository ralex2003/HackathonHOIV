# Protein Friend Finder - Implementation Guide

This guide provides step-by-step instructions to build the Protein Friend Finder application from scratch.

## Overview
The Protein Friend Finder is a web application that:
- Searches for proteins by name or UniProt ID
- Visualizes protein-protein interaction networks as interactive graphs
- Extracts interaction data from PubMed papers using LLM-based NER
- Displays supporting papers and detailed protein information

## Prerequisites
- Python 3.8+
- Node.js 16+
- pip (Python package manager)
- npm or yarn (Node package manager)

## Phase 1: Project Setup

### 1.1 Create Project Structure
```bash
cd C:\Users\aless\OneDrive\Desktop\Hackathon
mkdir app
mkdir app\services
mkdir app\models
mkdir app\utils
mkdir frontend
mkdir frontend\src
mkdir frontend\src\components
mkdir frontend\src\context
mkdir frontend\src\services
```

### 1.2 Create Python Requirements File
Create `requirements.txt`:
```
Flask==3.0.0
Flask-CORS==4.0.0
biopython==1.83
aiohttp==3.9.1
asyncio==3.4.3
```

Install Python dependencies:
```bash
pip install -r requirements.txt
```

### 1.3 Initialize React Frontend
```bash
cd frontend
npx create-react-app . --template typescript
npm install @mui/material @emotion/react @emotion/styled vis-network vis-data axios
```

## Phase 2: Backend Implementation

### 2.1 Flask Application Initialization
Create `app/__init__.py`:
```python
from flask import Flask
from flask_cors import CORS

def create_app():
    app = Flask(__name__)
    CORS(app)
    
    # Register routes
    from app.routes import bp
    app.register_blueprint(bp)
    
    return app

app = create_app()
```

### 2.2 Data Models

Create `app/models/protein.py`:
```python
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
```

Create `app/models/interaction.py`:
```python
from dataclasses import dataclass
from typing import List, Optional
from app.models.paper import Paper

@dataclass
class Interaction:
    source_protein: str
    target_protein: str
    interaction_type: str  # physical_binding, regulatory, complex_formation, genetic
    papers: List[Paper]
    context: Optional[str] = None
    
    # Interaction types for categorization
    TYPES = {
        'physical_binding': 'Physical Binding',
        'regulatory': 'Regulatory',
        'complex_formation': 'Complex Formation',
        'genetic': 'Genetic'
    }
```

Create `app/models/paper.py`:
```python
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
```

### 2.3 Utility Classes

Create `app/utils/cache.py`:
```python
import time
from typing import Dict, Any, Optional

class SimpleCache:
    def __init__(self, ttl: int = 3600):
        self.cache: Dict[str, tuple] = {}
        self.ttl = ttl
    
    def get(self, key: str) -> Optional[Any]:
        if key in self.cache:
            value, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl:
                return value
            else:
                del self.cache[key]
        return None
    
    def set(self, key: str, value: Any) -> None:
        self.cache[key] = (value, time.time())
    
    def clear(self) -> None:
        self.cache.clear()

# Global cache instance
cache = SimpleCache(ttl=3600)  # 1 hour TTL
```

Create `app/utils/rate_limiter.py`:
```python
import asyncio
from typing import Callable, Any

class AsyncRateLimiter:
    def __init__(self, rate_limit: int = 3):
        self.semaphore = asyncio.Semaphore(rate_limit)
        self.rate_limit = rate_limit
    
    async def __aenter__(self):
        await self.semaphore.acquire()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # Small delay to respect rate limits
        await asyncio.sleep(0.35)  # ~3 requests per second
        self.semaphore.release()
```

### 2.4 Service Layer

Create `app/services/pubmed_service.py`:
```python
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
```

Create `app/services/protein_service.py`:
```python
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
                name=record.name,
                description=record.description,
                function=self._extract_function(record),
                pathways=self._extract_pathways(record),
                localization=self._extract_localization(record)
            )
            
            cache.set(cache_key, protein)
            return protein
        except Exception as e:
            print(f"Error fetching protein {uniprot_id}: {e}")
            return None
    
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
        # This is a simplified extraction
        # In real implementation, parse the CC - FUNCTION field
        comments = record.annotations.get('comment', '')
        if 'FUNCTION:' in comments:
            func_start = comments.index('FUNCTION:') + 9
            func_end = comments.find('.', func_start)
            if func_end != -1:
                return comments[func_start:func_end].strip()
        return "Function information not available"
    
    def _extract_pathways(self, record) -> str:
        """Extract pathway information from SwissProt record"""
        comments = record.annotations.get('comment', '')
        if 'PATHWAY:' in comments:
            path_start = comments.index('PATHWAY:') + 8
            path_end = comments.find('.', path_start)
            if path_end != -1:
                return comments[path_start:path_end].strip()
        return "Pathway information not available"
    
    def _extract_localization(self, record) -> str:
        """Extract subcellular localization from SwissProt record"""
        comments = record.annotations.get('comment', '')
        if 'SUBCELLULAR LOCATION:' in comments:
            loc_start = comments.index('SUBCELLULAR LOCATION:') + 20
            loc_end = comments.find('.', loc_start)
            if loc_end != -1:
                return comments[loc_start:loc_end].strip()
        return "Localization information not available"
```

Create `app/services/llm_service.py`:
```python
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
```

Create `app/services/graph_service.py`:
```python
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
```

### 2.5 API Routes

Create `app/routes.py`:
```python
from flask import Blueprint, request, jsonify
from app.services.pubmed_service import PubMedService
from app.services.protein_service import ProteinService
from app.services.llm_service import MockLLMService
from app.services.graph_service import GraphService
import asyncio

bp = Blueprint('api', __name__)

# Initialize services
pubmed_service = PubMedService()
protein_service = ProteinService()
llm_service = MockLLMService()
graph_service = GraphService()

@bp.route('/api/search', methods=['POST'])
def search_protein():
    """Search for protein by name or UniProt ID"""
    data = request.json
    query = data.get('query', '')
    
    if not query:
        return jsonify({'error': 'Query is required'}), 400
    
    # Try to get protein
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    protein = loop.run_until_complete(protein_service.get_protein_by_name(query))
    
    if not protein:
        return jsonify({'error': 'Protein not found'}), 404
    
    return jsonify({
        'uniprot_id': protein.uniprot_id,
        'name': protein.name,
        'description': protein.description
    })

@bp.route('/api/protein/<uniprot_id>', methods=['GET'])
def get_protein(uniprot_id):
    """Get detailed protein information"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    protein = loop.run_until_complete(protein_service.get_protein_by_id(uniprot_id))
    
    if not protein:
        return jsonify({'error': 'Protein not found'}), 404
    
    # Get LLM-generated description
    llm_info = loop.run_until_complete(llm_service.generate_protein_description(protein.name))
    
    return jsonify({
        'uniprot_id': protein.uniprot_id,
        'name': protein.name,
        'description': protein.description,
        'function': llm_info.get('function', protein.function),
        'pathways': llm_info.get('pathways', protein.pathways),
        'localization': llm_info.get('localization', protein.localization)
    })

@bp.route('/api/interactions', methods=['POST'])
def get_interactions():
    """Get interactions for a protein with specified depth"""
    data = request.json
    protein_id = data.get('protein_id')
    depth = data.get('depth', 2)
    
    if not protein_id:
        return jsonify({'error': 'Protein ID is required'}), 400
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Search for papers
    pmids = loop.run_until_complete(pubmed_service.search_papers(protein_id))
    
    # Fetch paper details
    papers = loop.run_until_complete(pubmed_service.fetch_multiple_papers(pmids))
    
    # Extract interactions using LLM
    interactions = []
    for paper in papers:
        if paper.abstract:
            extracted = loop.run_until_complete(
                llm_service.extract_interactions(paper.abstract, protein_id)
            )
            
            for interaction_data in extracted.get('interactions', []):
                from app.models.interaction import Interaction
                interactions.append(Interaction(
                    source_protein=interaction_data['source_protein'],
                    target_protein=interaction_data['target_protein'],
                    interaction_type=interaction_data['interaction_type'],
                    papers=[paper],
                    context=interaction_data.get('context')
                ))
    
    return jsonify({
        'interactions': [
            {
                'source_protein': i.source_protein,
                'target_protein': i.target_protein,
                'interaction_type': i.interaction_type,
                'paper_count': len(i.papers),
                'papers': [{'pmid': p.pmid, 'title': p.title, 'year': p.year, 'url': p.url} for p in i.papers]
            }
            for i in interactions
        ],
        'total_papers': len(papers)
    })

@bp.route('/api/papers/<pmid>', methods=['GET'])
def get_paper(pmid):
    """Get detailed paper information"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    paper = loop.run_until_complete(pubmed_service.fetch_paper_details(pmid))
    
    if not paper:
        return jsonify({'error': 'Paper not found'}), 404
    
    return jsonify({
        'pmid': paper.pmid,
        'title': paper.title,
        'authors': paper.authors,
        'year': paper.year,
        'abstract': paper.abstract,
        'url': paper.url
    })

@bp.route('/api/graph', methods=['POST'])
def get_graph():
    """Get graph data for visualization"""
    data = request.json
    protein_id = data.get('protein_id')
    depth = data.get('depth', 2)
    filters = data.get('filters', None)  # List of interaction types to include
    
    if not protein_id:
        return jsonify({'error': 'Protein ID is required'}), 400
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Get protein info
    protein = loop.run_until_complete(protein_service.get_protein_by_id(protein_id))
    if not protein:
        return jsonify({'error': 'Protein not found'}), 404
    
    # Get interactions
    pmids = loop.run_until_complete(pubmed_service.search_papers(protein_id))
    papers = loop.run_until_complete(pubmed_service.fetch_multiple_papers(pmids))
    
    interactions = []
    for paper in papers:
        if paper.abstract:
            extracted = loop.run_until_complete(
                llm_service.extract_interactions(paper.abstract, protein_id)
            )
            
            for interaction_data in extracted.get('interactions', []):
                from app.models.interaction import Interaction
                interactions.append(Interaction(
                    source_protein=interaction_data['source_protein'],
                    target_protein=interaction_data['target_protein'],
                    interaction_type=interaction_data['interaction_type'],
                    papers=[paper],
                    context=interaction_data.get('context')
                ))
    
    # Build graph
    graph_data = graph_service.build_graph(protein, interactions, depth, filters)
    
    return jsonify(graph_data)
```

### 2.6 Main Entry Point
Create `run.py` in the root directory:
```python
from app import app

if __name__ == '__main__':
    app.run(debug=True, port=5000)
```

## Phase 3: Frontend Implementation

### 3.1 API Service
Create `frontend/src/services/api.js`:
```javascript
import axios from 'axios';

const API_BASE_URL = 'http://localhost:5000';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

export const searchProtein = async (query) => {
  const response = await api.post('/api/search', { query });
  return response.data;
};

export const getProtein = async (uniprotId) => {
  const response = await api.get(`/api/protein/${uniprotId}`);
  return response.data;
};

export const getInteractions = async (proteinId, depth = 2) => {
  const response = await api.post('/api/interactions', { protein_id: proteinId, depth });
  return response.data;
};

export const getPaper = async (pmid) => {
  const response = await api.get(`/api/papers/${pmid}`);
  return response.data;
};

export const getGraph = async (proteinId, depth = 2, filters = null) => {
  const response = await api.post('/api/graph', { 
    protein_id: proteinId, 
    depth,
    filters 
  });
  return response.data;
};

export default api;
```

### 3.2 React Context
Create `frontend/src/context/AppContext.js`:
```javascript
import React, { createContext, useContext, useReducer, useState } from 'react';

const AppContext = createContext();

const initialState = {
  currentProtein: null,
  graphData: null,
  selectedNode: null,
  selectedEdge: null,
  papers: [],
  loading: false,
  error: null,
  filters: {
    physical_binding: true,
    regulatory: true,
    complex_formation: true,
    genetic: true,
  },
  depth: 2,
};

function appReducer(state, action) {
  switch (action.type) {
    case 'SET_CURRENT_PROTEIN':
      return { ...state, currentProtein: action.payload };
    case 'SET_GRAPH_DATA':
      return { ...state, graphData: action.payload };
    case 'SET_SELECTED_NODE':
      return { ...state, selectedNode: action.payload };
    case 'SET_SELECTED_EDGE':
      return { ...state, selectedEdge: action.payload };
    case 'SET_PAPERS':
      return { ...state, papers: action.payload };
    case 'SET_LOADING':
      return { ...state, loading: action.payload };
    case 'SET_ERROR':
      return { ...state, error: action.payload };
    case 'TOGGLE_FILTER':
      return {
        ...state,
        filters: {
          ...state.filters,
          [action.payload]: !state.filters[action.payload],
        },
      };
    case 'SET_DEPTH':
      return { ...state, depth: action.payload };
    case 'RESET':
      return initialState;
    default:
      return state;
  }
}

export function AppProvider({ children }) {
  const [state, dispatch] = useReducer(appReducer, initialState);
  const [proteinDetailsOpen, setProteinDetailsOpen] = useState(false);
  const [paperListOpen, setPaperListOpen] = useState(false);

  const value = {
    state,
    dispatch,
    proteinDetailsOpen,
    setProteinDetailsOpen,
    paperListOpen,
    setPaperListOpen,
  };

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp() {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error('useApp must be used within AppProvider');
  }
  return context;
}
```

### 3.3 Search Bar Component
Create `frontend/src/components/SearchBar.js`:
```javascript
import React, { useState } from 'react';
import { TextField, Button, Box, Slider, Typography } from '@mui/material';
import { Search } from '@mui/icons-material';
import { useApp } from '../context/AppContext';
import { searchProtein, getGraph } from '../services/api';

function SearchBar() {
  const { state, dispatch } = useApp();
  const [query, setQuery] = useState('');
  const [localDepth, setLocalDepth] = useState(2);

  const handleSearch = async () => {
    if (!query.trim()) return;

    dispatch({ type: 'SET_LOADING', payload: true });
    dispatch({ type: 'SET_ERROR', payload: null });

    try {
      // Search for protein
      const protein = await searchProtein(query);
      dispatch({ type: 'SET_CURRENT_PROTEIN', payload: protein });

      // Get graph data
      const activeFilters = Object.keys(state.filters).filter(
        (key) => state.filters[key]
      );
      const graphData = await getGraph(protein.uniprot_id, localDepth, activeFilters);
      dispatch({ type: 'SET_GRAPH_DATA', payload: graphData });
    } catch (error) {
      dispatch({ type: 'SET_ERROR', payload: error.message });
    } finally {
      dispatch({ type: 'SET_LOADING', payload: false });
    }
  };

  const handleKeyPress = (e) => {
    if (e.key === 'Enter') {
      handleSearch();
    }
  };

  return (
    <Box sx={{ p: 2, bgcolor: 'background.paper', boxShadow: 1 }}>
      <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', mb: 2 }}>
        <TextField
          fullWidth
          variant="outlined"
          placeholder="Enter protein name or UniProt ID (e.g., p53, P04637)"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyPress={handleKeyPress}
          disabled={state.loading}
        />
        <Button
          variant="contained"
          startIcon={<Search />}
          onClick={handleSearch}
          disabled={state.loading || !query.trim()}
        >
          Search
        </Button>
      </Box>
      
      <Box sx={{ px: 1 }}>
        <Typography variant="body2" gutterBottom>
          Graph Depth: {localDepth}
        </Typography>
        <Slider
          value={localDepth}
          onChange={(e, value) => setLocalDepth(value)}
          min={1}
          max={5}
          marks
          valueLabelDisplay="auto"
          disabled={state.loading}
        />
      </Box>
    </Box>
  );
}

export default SearchBar;
```

### 3.4 Graph Visualization Component
Create `frontend/src/components/GraphVisualization.js`:
```javascript
import React, { useEffect, useRef } from 'react';
import { Network } from 'vis-network/standalone';
import { Box, IconButton, Tooltip } from '@mui/material';
import { ZoomIn, ZoomOut, Refresh, Download } from '@mui/icons-material';
import { useApp } from '../context/AppContext';

function GraphVisualization() {
  const { state, dispatch, setProteinDetailsOpen, setPaperListOpen } = useApp();
  const networkRef = useRef(null);
  const containerRef = useRef(null);
  const networkInstance = useRef(null);

  useEffect(() => {
    if (containerRef.current && state.graphData) {
      // Destroy existing network
      if (networkInstance.current) {
        networkInstance.current.destroy();
      }

      const options = {
        nodes: {
          shape: 'dot',
          size: 20,
          font: {
            size: 14,
            face: 'Arial',
          },
          borderWidth: 2,
        },
        edges: {
          width: 2,
          color: { inherit: 'from' },
          smooth: {
            type: 'continuous',
          },
          font: {
            size: 10,
            align: 'middle',
          },
        },
        physics: {
          stabilization: true,
          barnesHut: {
            gravitationalConstant: -2000,
            centralGravity: 0.3,
            springLength: 150,
            springConstant: 0.04,
          },
        },
        interaction: {
          hover: true,
          tooltipDelay: 200,
        },
      };

      const data = {
        nodes: new vis.DataSet(state.graphData.nodes),
        edges: new vis.DataSet(state.graphData.edges),
      };

      networkInstance.current = new Network(containerRef.current, data, options);

      // Handle node click
      networkInstance.current.on('click', (params) => {
        if (params.nodes.length > 0) {
          const nodeId = params.nodes[0];
          const node = state.graphData.nodes.find((n) => n.id === nodeId);
          if (node) {
            dispatch({ type: 'SET_SELECTED_NODE', payload: node });
            setProteinDetailsOpen(true);
          }
        }
      });

      // Handle edge click
      networkInstance.current.on('click', (params) => {
        if (params.edges.length > 0) {
          const edgeId = params.edges[0];
          const edge = state.graphData.edges.find((e) => 
            e.from === params.edges[0].split('-')[0] && 
            e.to === params.edges[0].split('-')[1]
          );
          if (edge) {
            dispatch({ type: 'SET_SELECTED_EDGE', payload: edge });
            dispatch({ type: 'SET_PAPERS', payload: edge.papers });
            setPaperListOpen(true);
          }
        }
      });
    }
  }, [state.graphData, dispatch, setProteinDetailsOpen, setPaperListOpen]);

  const handleZoomIn = () => {
    if (networkInstance.current) {
      const scale = networkInstance.current.getScale();
      networkInstance.current.moveTo({ scale: scale * 1.2 });
    }
  };

  const handleZoomOut = () => {
    if (networkInstance.current) {
      const scale = networkInstance.current.getScale();
      networkInstance.current.moveTo({ scale: scale * 0.8 });
    }
  };

  const handleReset = () => {
    if (networkInstance.current) {
      networkInstance.current.fit();
    }
  };

  const handleExport = () => {
    if (networkInstance.current) {
      const canvas = containerRef.current.querySelector('canvas');
      const url = canvas.toDataURL('image/png');
      const link = document.createElement('a');
      link.download = 'protein-network.png';
      link.href = url;
      link.click();
    }
  };

  return (
    <Box sx={{ position: 'relative', height: '100%', width: '100%' }}>
      <div ref={containerRef} style={{ height: '100%', width: '100%' }} />
      
      <Box sx={{ position: 'absolute', top: 16, right: 16, display: 'flex', gap: 1 }}>
        <Tooltip title="Zoom In">
          <IconButton onClick={handleZoomIn} size="small">
            <ZoomIn />
          </IconButton>
        </Tooltip>
        <Tooltip title="Zoom Out">
          <IconButton onClick={handleZoomOut} size="small">
            <ZoomOut />
          </IconButton>
        </Tooltip>
        <Tooltip title="Reset View">
          <IconButton onClick={handleReset} size="small">
            <Refresh />
          </IconButton>
        </Tooltip>
        <Tooltip title="Export Image">
          <IconButton onClick={handleExport} size="small">
            <Download />
          </IconButton>
        </Tooltip>
      </Box>
    </Box>
  );
}

export default GraphVisualization;
```

### 3.5 Protein Details Panel
Create `frontend/src/components/ProteinDetailsPanel.js`:
```javascript
import React from 'react';
import {
  Drawer,
  Box,
  Typography,
  IconButton,
  Divider,
  Button,
  Chip,
} from '@mui/material';
import { Close } from '@mui/icons-material';
import { useApp } from '../context/AppContext';
import { getProtein, getGraph } from '../services/api';

function ProteinDetailsPanel() {
  const { state, dispatch, proteinDetailsOpen, setProteinDetailsOpen } = useApp();

  const handleClose = () => {
    setProteinDetailsOpen(false);
    dispatch({ type: 'SET_SELECTED_NODE', payload: null });
  };

  const handleLoadConnections = async () => {
    if (!state.selectedNode) return;

    dispatch({ type: 'SET_LOADING', payload: true });

    try {
      const activeFilters = Object.keys(state.filters).filter(
        (key) => state.filters[key]
      );
      const graphData = await getGraph(
        state.selectedNode.id,
        state.depth,
        activeFilters
      );
      dispatch({ type: 'SET_GRAPH_DATA', payload: graphData });
    } catch (error) {
      dispatch({ type: 'SET_ERROR', payload: error.message });
    } finally {
      dispatch({ type: 'SET_LOADING', payload: false });
    }
  };

  const loadProteinDetails = async () => {
    if (!state.selectedNode) return;

    try {
      const protein = await getProtein(state.selectedNode.id);
      dispatch({ type: 'SET_SELECTED_NODE', payload: { ...state.selectedNode, ...protein } });
    } catch (error) {
      console.error('Error loading protein details:', error);
    }
  };

  React.useEffect(() => {
    if (state.selectedNode && proteinDetailsOpen) {
      loadProteinDetails();
    }
  }, [state.selectedNode, proteinDetailsOpen]);

  if (!state.selectedNode) return null;

  return (
    <Drawer
      anchor="right"
      open={proteinDetailsOpen}
      onClose={handleClose}
      sx={{
        width: 400,
        flexShrink: 0,
        '& .MuiDrawer-paper': {
          width: 400,
          boxSizing: 'border-box',
        },
      }}
    >
      <Box sx={{ p: 2, height: '100%', overflow: 'auto' }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
          <Typography variant="h6">{state.selectedNode.label}</Typography>
          <IconButton onClick={handleClose} size="small">
            <Close />
          </IconButton>
        </Box>

        <Divider sx={{ mb: 2 }} />

        <Typography variant="subtitle2" gutterBottom>
          UniProt ID
        </Typography>
        <Chip label={state.selectedNode.id} size="small" sx={{ mb: 2 }} />

        {state.selectedNode.description && (
          <>
            <Typography variant="subtitle2" gutterBottom>
              Description
            </Typography>
            <Typography variant="body2" sx={{ mb: 2 }}>
              {state.selectedNode.description}
            </Typography>
          </>
        )}

        {state.selectedNode.function && (
          <>
            <Typography variant="subtitle2" gutterBottom>
              Function
            </Typography>
            <Typography variant="body2" sx={{ mb: 2 }}>
              {state.selectedNode.function}
            </Typography>
          </>
        )}

        {state.selectedNode.pathways && (
          <>
            <Typography variant="subtitle2" gutterBottom>
              Pathways
            </Typography>
            <Typography variant="body2" sx={{ mb: 2 }}>
              {state.selectedNode.pathways}
            </Typography>
          </>
        )}

        {state.selectedNode.localization && (
          <>
            <Typography variant="subtitle2" gutterBottom>
              Localization
            </Typography>
            <Typography variant="body2" sx={{ mb: 2 }}>
              {state.selectedNode.localization}
            </Typography>
          </>
        )}

        <Divider sx={{ my: 2 }} />

        <Button
          variant="contained"
          fullWidth
          onClick={handleLoadConnections}
          disabled={state.loading}
        >
          Load Connections
        </Button>
      </Box>
    </Drawer>
  );
}

export default ProteinDetailsPanel;
```

### 3.6 Paper List Panel
Create `frontend/src/components/PaperListPanel.js`:
```javascript
import React, { useEffect, useState } from 'react';
import {
  Drawer,
  Box,
  Typography,
  IconButton,
  Divider,
  List,
  ListItem,
  ListItemText,
  Link,
  Chip,
  Tooltip,
} from '@mui/material';
import { Close } from '@mui/icons-material';
import { useApp } from '../context/AppContext';
import { getPaper } from '../services/api';

function PaperListPanel() {
  const { state, dispatch, paperListOpen, setPaperListOpen } = useApp();
  const [paperDetails, setPaperDetails] = useState({});

  const handleClose = () => {
    setPaperListOpen(false);
    dispatch({ type: 'SET_SELECTED_EDGE', payload: null });
    dispatch({ type: 'SET_PAPERS', payload: [] });
  };

  const loadPaperDetails = async (pmid) => {
    if (paperDetails[pmid]) return;

    try {
      const paper = await getPaper(pmid);
      setPaperDetails((prev) => ({ ...prev, [pmid]: paper }));
    } catch (error) {
      console.error('Error loading paper details:', error);
    }
  };

  useEffect(() => {
    if (state.papers.length > 0) {
      state.papers.forEach((pmid) => loadPaperDetails(pmid));
    }
  }, [state.papers]);

  if (!state.selectedEdge) return null;

  return (
    <Drawer
      anchor="left"
      open={paperListOpen}
      onClose={handleClose}
      sx={{
        width: 400,
        flexShrink: 0,
        '& .MuiDrawer-paper': {
          width: 400,
          boxSizing: 'border-box',
        },
      }}
    >
      <Box sx={{ p: 2, height: '100%', overflow: 'auto' }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
          <Typography variant="h6">Related Papers</Typography>
          <IconButton onClick={handleClose} size="small">
            <Close />
          </IconButton>
        </Box>

        <Divider sx={{ mb: 2 }} />

        <Typography variant="body2" sx={{ mb: 2 }}>
          {state.selectedEdge.from} → {state.selectedEdge.to}
        </Typography>

        <Chip
          label={`${state.selectedEdge.paper_count} papers`}
          size="small"
          sx={{ mb: 2 }}
        />

        <List>
          {state.papers.map((pmid) => {
            const paper = paperDetails[pmid];
            return (
              <ListItem key={pmid} alignItems="flex-start">
                <ListItemText
                  primary={
                    <Link
                      href={paper?.url || `https://pubmed.ncbi.nlm.nih.gov/${pmid}/`}
                      target="_blank"
                      rel="noopener noreferrer"
                      underline="hover"
                    >
                      {paper?.title || `Paper ${pmid}`}
                    </Link>
                  }
                  secondary={
                    <React.Fragment>
                      <Typography variant="body2" component="span">
                        {paper?.year || 'Unknown year'}
                      </Typography>
                      {paper?.abstract && (
                        <Tooltip title={paper.abstract}>
                          <Typography
                            variant="caption"
                            sx={{ display: 'block', mt: 0.5, cursor: 'help' }}
                          >
                            Hover for abstract
                          </Typography>
                        </Tooltip>
                      )}
                    </React.Fragment>
                  }
                />
              </ListItem>
            );
          })}
        </List>
      </Box>
    </Drawer>
  );
}

export default PaperListPanel;
```

### 3.7 Filter Controls Component
Create `frontend/src/components/FilterControls.js`:
```javascript
import React from 'react';
import { Box, FormControlLabel, Checkbox, Typography, Paper } from '@mui/material';
import { useApp } from '../context/AppContext';

function FilterControls() {
  const { state, dispatch } = useApp();

  const interactionTypes = [
    { key: 'physical_binding', label: 'Physical Binding' },
    { key: 'regulatory', label: 'Regulatory' },
    { key: 'complex_formation', label: 'Complex Formation' },
    { key: 'genetic', label: 'Genetic' },
  ];

  const handleToggle = (key) => {
    dispatch({ type: 'TOGGLE_FILTER', payload: key });
  };

  return (
    <Paper sx={{ p: 2, mb: 2 }}>
      <Typography variant="subtitle2" gutterBottom>
        Filter by Interaction Type
      </Typography>
      {interactionTypes.map((type) => (
        <FormControlLabel
          key={type.key}
          control={
            <Checkbox
              checked={state.filters[type.key]}
              onChange={() => handleToggle(type.key)}
              size="small"
            />
          }
          label={type.label}
        />
      ))}
    </Paper>
  );
}

export default FilterControls;
```

### 3.8 Main App Component
Update `frontend/src/App.js`:
```javascript
import React from 'react';
import { ThemeProvider, createTheme, CssBaseline, Box, Alert, CircularProgress } from '@mui/material';
import { AppProvider, useApp } from './context/AppContext';
import SearchBar from './components/SearchBar';
import GraphVisualization from './components/GraphVisualization';
import ProteinDetailsPanel from './components/ProteinDetailsPanel';
import PaperListPanel from './components/PaperListPanel';
import FilterControls from './components/FilterControls';

const theme = createTheme({
  palette: {
    primary: {
      main: '#1976d2',
    },
    secondary: {
      main: '#dc004e',
    },
  },
});

function AppContent() {
  const { state } = useApp();

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      <SearchBar />
      
      <Box sx={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        <Box sx={{ width: 300, p: 2, overflow: 'auto' }}>
          <FilterControls />
        </Box>
        
        <Box sx={{ flex: 1, position: 'relative' }}>
          {state.loading && (
            <Box
              sx={{
                position: 'absolute',
                top: '50%',
                left: '50%',
                transform: 'translate(-50%, -50%)',
                zIndex: 1000,
              }}
            >
              <CircularProgress />
            </Box>
          )}
          
          {state.error && (
            <Alert severity="error" sx={{ m: 2 }}>
              {state.error}
            </Alert>
          )}
          
          {state.graphData ? (
            <GraphVisualization />
          ) : (
            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                height: '100%',
                color: 'text.secondary',
              }}
            >
              <Typography variant="h6">
                Search for a protein to visualize its interaction network
              </Typography>
            </Box>
          )}
        </Box>
      </Box>
      
      <ProteinDetailsPanel />
      <PaperListPanel />
    </Box>
  );
}

function App() {
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <AppProvider>
        <AppContent />
      </AppProvider>
    </ThemeProvider>
  );
}

export default App;
```

## Phase 4: Running the Application

### 4.1 Start Backend
```bash
cd C:\Users\aless\OneDrive\Desktop\Hackathon
python run.py
```

The Flask server will start on `http://localhost:5000`

### 4.2 Start Frontend
```bash
cd frontend
npm start
```

The React app will open on `http://localhost:3000`

## Phase 5: Testing

### 5.1 Test Protein Search
1. Open `http://localhost:3000`
2. Enter "p53" or "P04637" in the search bar
3. Click Search
4. Verify graph visualization appears

### 5.2 Test Graph Interactions
1. Click on a node in the graph
2. Verify protein details panel opens on the right
3. Click on an edge
4. Verify paper list panel opens on the left

### 5.3 Test Filters
1. Toggle interaction type filters
2. Verify graph updates to show/hide specific interaction types

### 5.4 Test Depth Control
1. Adjust the depth slider
2. Search again
3. Verify graph complexity changes

### 5.5 Test Export
1. Click the export button
2. Verify PNG image downloads

## Phase 6: Future Enhancements

### 6.1 Replace Mock LLM
When ready to integrate Groq or Kimi:
1. Update `app/services/llm_service.py`
2. Replace mock methods with actual API calls
3. Ensure response format matches the mock structure

### 6.2 Add Persistent Storage
- Replace in-memory cache with Redis or database
- Store user searches and interaction history

### 6.3 Improve Protein Mapping
- Implement full UniProt mapping API integration
- Add fuzzy matching for protein names
- Cache common protein mappings

### 6.4 Performance Optimizations
- Implement request batching for PubMed
- Add pagination for large paper sets
- Optimize graph rendering for large networks

### 6.5 Additional Features
- User authentication and saved searches
- Export graph data in various formats (JSON, CSV)
- Compare multiple proteins
- Pathway visualization integration
- Real-time collaboration features

## Troubleshooting

### PubMed API Issues
- If you encounter rate limit errors, increase the delay in `rate_limiter.py`
- Consider registering for a PubMed API key for higher limits

### BioPython Issues
- Ensure BioPython is properly installed: `pip install biopython`
- Check network connectivity for UniProt API access

### React Build Issues
- Clear node_modules and reinstall: `rm -rf node_modules && npm install`
- Ensure Node.js version is 16 or higher

### CORS Issues
- Verify Flask-CORS is properly configured in `app/__init__.py`
- Check that frontend API calls use the correct backend URL

## Conclusion

This implementation guide provides a complete roadmap to build the Protein Friend Finder application. The modular architecture allows for easy replacement of components (especially the LLM service) as requirements evolve. The mock implementations ensure the system is testable and functional even before integrating external AI services.