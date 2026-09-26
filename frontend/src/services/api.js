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
