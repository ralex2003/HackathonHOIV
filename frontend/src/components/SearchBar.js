import React, { useState } from 'react';
import {
  TextField, Button, Box, Slider, Typography, InputAdornment
} from '@mui/material';
import { Search as SearchIcon } from '@mui/icons-material';
import { useApp } from '../context/AppContext';
import { searchProtein, getGraph } from '../services/api';
import { searchLogger } from '../utils/logger';

function SearchBar() {
  const { state, dispatch } = useApp();
  const [query, setQuery] = useState('');
  const logger = searchLogger;
  const [localDepth, setLocalDepth] = useState(state.depth || 2);
  const [localEdgesPerNode, setLocalEdgesPerNode] = useState(
    state.edgesPerNode || 5
  );

  const handleDepthChange = (event, value) => {
    setLocalDepth(value);
    dispatch({ type: 'SET_DEPTH', payload: value });
    logger.debug(`Depth changed to ${value}`);
  };

  const handleEdgesPerNodeChange = (event, value) => {
    setLocalEdgesPerNode(value);
    dispatch({ type: 'SET_EDGES_PER_NODE', payload: value });
    logger.debug(`Edges per node changed to ${value}`);
  };

  const handleSearch = async () => {
    if (!query.trim()) {
      logger.warn("Search attempted with empty query");
      return;
    }

    logger.info(
      `Search initiated: "${query}" (depth=${localDepth}, ` +
      `edgesPerNode=${localEdgesPerNode})`
    );
    dispatch({ type: 'SET_LOADING', payload: true });
    dispatch({ type: 'SET_ERROR', payload: null });

    try {
      logger.debug("Calling searchProtein API...");
      const protein = await searchProtein(query);
      logger.info(`Protein found: "${protein.name}" (UniProt: ${protein.uniprot_id})`);
      dispatch({ type: 'SET_CURRENT_PROTEIN', payload: protein });

      const activeFilters = Object.keys(state.filters).filter(
        (key) => state.filters[key]
      );
      logger.debug(`Active filters: ${activeFilters.join(', ') || 'none'}`);
      logger.debug("Calling getGraph API...");
      const graphData = await getGraph(
        protein.uniprot_id,
        localDepth,
        activeFilters.length ? activeFilters : null,
        localEdgesPerNode
      );
      logger.info(`Graph loaded: ${graphData.nodes?.length || 0} nodes, ${graphData.edges?.length || 0} edges`);
      dispatch({ type: 'SET_GRAPH_DATA', payload: graphData });
    } catch (error) {
      logger.error(`Search failed: ${error.response?.data?.error || error.message}`);
      dispatch({
        type: 'SET_ERROR',
        payload: error.response?.data?.error || error.message,
      });
    } finally {
      dispatch({ type: 'SET_LOADING', payload: false });
      logger.debug("Search complete, loading state reset");
    }
  };

  const handleKeyPress = (e) => {
    if (e.key === 'Enter') {
      handleSearch();
    }
  };

  return (
    <Box
      sx={{
        mx: 4,
        mt: 3,
        mb: 2,
        p: 3,
        borderRadius: 3,
        background: 'rgba(26, 26, 46, 0.8)',
        backdropFilter: 'blur(20px)',
        border: '1px solid rgba(255,255,255,0.06)',
        boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
      }}
    >
      <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', mb: 2 }}>
        <TextField
          fullWidth
          variant="outlined"
          placeholder="Search protein name or UniProt ID (e.g., p53, P04637)"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyPress={handleKeyPress}
          disabled={state.loading}
          size="small"
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon sx={{ color: '#6C63FF', fontSize: 20 }} />
              </InputAdornment>
            ),
            sx: {
              backgroundColor: '#0D0D1A',
              borderRadius: 2,
            },
          }}
        />
        <Button
          variant="contained"
          startIcon={<SearchIcon />}
          onClick={handleSearch}
          disabled={state.loading || !query.trim()}
          sx={{
            minWidth: 120,
            height: 40,
            fontSize: '0.9rem',
          }}
        >
          Search
        </Button>
      </Box>

      <Box sx={{ px: 1, display: 'flex', alignItems: 'center', gap: 2 }}>
        <Typography
          variant="caption"
          sx={{ color: '#A0A0B8', whiteSpace: 'nowrap', fontWeight: 500 }}
        >
          Depth: {localDepth}
        </Typography>
        <Slider
          value={localDepth}
          onChange={handleDepthChange}
          min={1}
          max={5}
          marks={[
            { value: 1, label: '1' },
            { value: 2, label: '2' },
            { value: 3, label: '3' },
            { value: 4, label: '4' },
            { value: 5, label: '5' },
          ]}
          valueLabelDisplay="auto"
          disabled={state.loading}
          sx={{ flex: 1 }}
        />
      </Box>

      <Box sx={{ px: 1, display: 'flex', alignItems: 'center', gap: 2, mt: 1.5 }}>
        <Typography
          variant="caption"
          sx={{ color: '#A0A0B8', whiteSpace: 'nowrap', fontWeight: 500 }}
        >
          Edges per node: {localEdgesPerNode}
        </Typography>
        <Slider
          value={localEdgesPerNode}
          onChange={handleEdgesPerNodeChange}
          min={1}
          max={25}
          step={1}
          marks={[
            { value: 1, label: '1' },
            { value: 5, label: '5' },
            { value: 10, label: '10' },
            { value: 15, label: '15' },
            { value: 20, label: '20' },
            { value: 25, label: '25' },
          ]}
          valueLabelDisplay="auto"
          disabled={state.loading}
          aria-label="Maximum new edges per protein node"
          sx={{ flex: 1 }}
        />
      </Box>

      <Typography
        variant="caption"
        sx={{ color: '#6A6A85', display: 'block', mt: 1, px: 1 }}
      >
        Each protein contributes up to {localEdgesPerNode} NEW partners of its
        own, so every layer grows {localEdgesPerNode}× wider. Extra edges back
        to proteins already in the graph are still kept, but don't count
        toward this number.
      </Typography>
    </Box>
  );
}

export default SearchBar;
