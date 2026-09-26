import axios from 'axios';
import { apiLogger } from '../utils/logger';

const API_BASE_URL = 'http://localhost:5000';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

/** Request interceptor: log every outgoing request */
api.interceptors.request.use((config) => {
  apiLogger.info(`${config.method?.toUpperCase()} ${config.url}`, config.data || '');
  return config;
}, (error) => {
  apiLogger.error('Request interceptor error:', error);
  return Promise.reject(error);
});

/** Response interceptor: log every response or error */
api.interceptors.response.use(
  (response) => {
    apiLogger.debug(`${response.status} ${response.config.url}`, response.data);
    return response;
  },
  (error) => {
    apiLogger.error(`Response error: ${error.config?.method} ${error.config?.url}`, error.message);
    return Promise.reject(error);
  }
);

export const searchProtein = async (query) => {
  apiLogger.info(`Searching protein: "${query}"`);
  const response = await api.post('/api/search', { query });
  apiLogger.info(`Search result: ${response.data.name || 'N/A'} (UniProt: ${response.data.uniprot_id || 'N/A'})`);
  return response.data;
};

export const getProtein = async (uniprotId) => {
  apiLogger.info(`Fetching protein details: ${uniprotId}`);
  const response = await api.get(`/api/protein/${uniprotId}`);
  apiLogger.info(`Protein details loaded: ${response.data.name || uniprotId}`);
  return response.data;
};

export const getInteractions = async (proteinId, depth = 2) => {
  apiLogger.info(`Fetching interactions: protein=${proteinId}, depth=${depth}`);
  const response = await api.post('/api/interactions', { protein_id: proteinId, depth });
  apiLogger.info(`Interactions loaded: ${response.data.total_interactions || 0} interactions, ${response.data.total_pmids || 0} papers`);
  return response.data;
};

export const getPaper = async (pmid) => {
  apiLogger.info(`Fetching paper: PMID=${pmid}`);
  const response = await api.get(`/api/papers/${pmid}`);
  apiLogger.info(`Paper loaded: ${response.data.title?.slice(0, 50) || 'N/A'}`);
  return response.data;
};

export const getGraph = async (
  proteinId,
  depth = 2,
  filters = null,
  edgesPerNode = 5
) => {
  apiLogger.info(
    `Fetching graph: protein=${proteinId}, depth=${depth}, ` +
    `edgesPerNode=${edgesPerNode}, filters=${JSON.stringify(filters)}`
  );
  const response = await api.post('/api/graph', {
    protein_id: proteinId,
    depth,
    filters,
    edges_per_node: edgesPerNode,
  });
  apiLogger.info(`Graph loaded: ${response.data.nodes?.length || 0} nodes, ${response.data.edges?.length || 0} edges`);
  return response.data;
};

export default api;
