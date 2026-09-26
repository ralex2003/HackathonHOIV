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
    if (state.selectedNode && proteinDetailsOpen && !state.selectedNode.function) {
      loadProteinDetails();
    }
  }, [state.selectedNode, proteinDetailsOpen]); // eslint-disable-line react-hooks/exhaustive-deps

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
