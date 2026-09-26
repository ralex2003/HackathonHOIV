import React from 'react';
import {
  Drawer,
  Box,
  Typography,
  IconButton,
  Divider,
  Button,
  Chip,
  TextField,
} from '@mui/material';
import { Close } from '@mui/icons-material';
import { useApp } from '../context/AppContext';
import { getProtein, getGraph } from '../services/api';
import { proteinLogger } from '../utils/logger';

function ProteinDetailsPanel() {
  const { state, dispatch, proteinDetailsOpen, setProteinDetailsOpen } = useApp();
  const logger = proteinLogger;

  const handleClose = () => {
    logger.debug("Closing protein details panel");
    setProteinDetailsOpen(false);
    dispatch({ type: 'SET_SELECTED_NODE', payload: null });
  };

  const handleLoadConnections = async () => {
    if (!state.selectedNode) return;

    logger.info(`Loading connections for: ${state.selectedNode.label || state.selectedNode.id}`);
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
      logger.info(`Connections loaded: ${graphData.nodes?.length || 0} nodes, ${graphData.edges?.length || 0} edges`);
      dispatch({ type: 'SET_GRAPH_DATA', payload: graphData });
    } catch (error) {
      logger.error(`Failed to load connections: ${error.message}`);
      dispatch({ type: 'SET_ERROR', payload: error.message });
    } finally {
      dispatch({ type: 'SET_LOADING', payload: false });
    }
  };

  const loadProteinDetails = async () => {
    if (!state.selectedNode) return;

    logger.debug(`Loading detailed info for: ${state.selectedNode.id}`);
    try {
      const protein = await getProtein(state.selectedNode.id);
      logger.info(`Protein details loaded: ${protein.name || state.selectedNode.id}`);
      dispatch({ type: 'SET_SELECTED_NODE', payload: { ...state.selectedNode, ...protein } });
    } catch (error) {
      logger.error('Error loading protein details:', error);
    }
  };

  React.useEffect(() => {
    if (state.selectedNode && proteinDetailsOpen && !state.selectedNode.function) {
      loadProteinDetails();
    }
  }, [state.selectedNode, proteinDetailsOpen]);

  if (!state.selectedNode) return null;

  const node = state.selectedNode;
  const accentColor = '#6C63FF';

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
          background: 'rgba(26, 26, 46, 0.95)',
          backdropFilter: 'blur(20px)',
          borderLeft: '1px solid rgba(255,255,255,0.06)',
          animation: 'slideInRight 0.3s ease-out',
        },
      }}
    >
      <Box sx={{ p: 3, height: '100%', overflow: 'auto' }}>
        {/* Header */}
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 3 }}>
          <Box>
            <Typography variant="h5" sx={{ color: '#E8E8F0', fontWeight: 700, letterSpacing: '-0.01em' }}>
              {node.label}
            </Typography>
            <Chip
              label={node.id}
              size="small"
              sx={{
                mt: 1,
                fontFamily: 'monospace',
                backgroundColor: 'rgba(108, 99, 255, 0.15)',
                border: '1px solid rgba(108, 99, 255, 0.3)',
                color: '#8B85FF',
              }}
            />
          </Box>
          <IconButton
            onClick={handleClose}
            size="small"
            sx={{
              backgroundColor: 'rgba(255,255,255,0.05)',
              border: '1px solid rgba(255,255,255,0.1)',
            }}
          >
            <Close sx={{ color: '#A0A0B8' }} />
          </IconButton>
        </Box>

        <Divider sx={{ mb: 3 }} />

        {/* Info sections */}
        <Box sx={{ mb: 3 }}>
          <Typography variant="subtitle2" sx={{ color: '#A0A0B8', mb: 1, fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            Description
          </Typography>
          <Typography variant="body2" sx={{ color: '#E8E8F0', lineHeight: 1.7, fontSize: '0.9rem' }}>
            {node.description}
          </Typography>
        </Box>

        {node.function && (
          <Box sx={{ mb: 3 }}>
            <Typography variant="subtitle2" sx={{ color: '#A0A0B8', mb: 1, fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
              Function
            </Typography>
            <Typography variant="body2" sx={{ color: '#E8E8F0', lineHeight: 1.7, fontSize: '0.9rem' }}>
              {node.function}
            </Typography>
          </Box>
        )}

        {node.pathways && (
          <Box sx={{ mb: 3 }}>
            <Typography variant="subtitle2" sx={{ color: '#A0A0B8', mb: 1, fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
              Pathways
            </Typography>
            <Typography variant="body2" sx={{ color: '#E8E8F0', lineHeight: 1.7, fontSize: '0.9rem' }}>
              {node.pathways}
            </Typography>
          </Box>
        )}

        {node.localization && (
          <Box sx={{ mb: 3 }}>
            <Typography variant="subtitle2" sx={{ color: '#A0A0B8', mb: 1, fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
              Localization
            </Typography>
            <Typography variant="body2" sx={{ color: '#E8E8F0', lineHeight: 1.7, fontSize: '0.9rem' }}>
              {node.localization}
            </Typography>
          </Box>
        )}

        {node.organism && (
          <Box sx={{ mb: 3 }}>
            <Typography variant="subtitle2" sx={{ color: '#A0A0B8', mb: 1, fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
              Organism
            </Typography>
            <Typography variant="body2" sx={{ color: '#E8E8F0', fontSize: '0.9rem' }}>
              {node.organism}
            </Typography>
          </Box>
        )}

        <Divider sx={{ my: 3 }} />

        <Button
          variant="contained"
          fullWidth
          onClick={handleLoadConnections}
          disabled={state.loading}
          sx={{
            height: 44,
            fontSize: '0.9rem',
            fontWeight: 600,
          }}
        >
          {state.loading ? 'Loading...' : 'Load Connections'}
        </Button>
      </Box>
    </Drawer>
  );
}

export default ProteinDetailsPanel;
