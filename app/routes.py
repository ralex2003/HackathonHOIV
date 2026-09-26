from flask import Blueprint, request, jsonify
from app.services.pubmed_service import PubMedService
from app.services.protein_service import ProteinService
from app.services.llm_service import MockLLMService
from app.services.graph_service import GraphService
from app.models.interaction import Interaction
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
