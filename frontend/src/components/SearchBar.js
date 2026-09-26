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
