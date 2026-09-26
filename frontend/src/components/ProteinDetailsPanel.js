import React, { useRef } from 'react';
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
  const selectedNodeRef = useRef(state.selectedNode);
  selectedNodeRef.current = state.selectedNode;

  const handleClose = () => {
    logger.debug("Closing protein details panel");
    setProteinDetailsOpen(false);
    dispatch({ type: 'SET_SELECTED_NODE', payload: null });
  };

  const handleLoadConnections = async () => {
    const node = selectedNodeRef.current;
    if (!node) return;

    logger.info(`Loading connections for: ${node.label || node.id}`);
    dispatch({ type: 'SET_LOADING', payload: true });

    try {
      const activeFilters = Object.keys(state.filters).filter(
        (key) => state.filters[key]
      );
      const graphData = await getGraph(
        node.id,
        state.depth,
        activeFilters,
        state.edgesPerNode
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
    const node = selectedNodeRef.current;
    if (!node) return;

    logger.debug(`Loading detailed info for: ${node.id}`);
    try {
      const protein = await getProtein(node.id);
      logger.info(`Protein details loaded: ${protein.name || node.id}`);
      dispatch({ type: 'SET_SELECTED_NODE', payload: { ...node, ...protein } });
    } catch (error) {
      logger.error('Error loading protein details:', error);
    }
  };

  const selectedNodeId = state.selectedNode?.id;

  React.useEffect(() => {
    const node = selectedNodeRef.current;
    if (proteinDetailsOpen && node && !node.function) {
      loadProteinDetails();
    }
  }, [proteinDetailsOpen, selectedNodeId]);

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
          <Typography variant="body2" sx={{ color: node.description ? '#E8E8F0' : '#A0A0B8', lineHeight: 1.7, fontSize: '0.9rem', fontStyle: node.description ? 'normal' : 'italic' }}>
            {node.description || 'Loading description...'}
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

        {/* LLM-generated summary */}
        {node.llm_summary && (
          <Box sx={{ mb: 3 }}>
            <Typography variant="subtitle2" sx={{ color: '#A0A0B8', mb: 1, fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
              AI Summary
            </Typography>
            {node.llm_summary.function && (
              <Box sx={{ mb: 1.5 }}>
                <Typography variant="body2" sx={{ color: '#E8E8F0', lineHeight: 1.7, fontSize: '0.85rem' }}>
                  <strong style={{ color: '#4ECDC4' }}>Function:</strong>{' '}
                  {node.llm_summary.function}
                </Typography>
              </Box>
            )}
            {node.llm_summary.pathways && (
              <Box sx={{ mb: 1.5 }}>
                <Typography variant="body2" sx={{ color: '#E8E8F0', lineHeight: 1.7, fontSize: '0.85rem' }}>
                  <strong style={{ color: '#F7DC6F' }}>Pathways:</strong>{' '}
                  {node.llm_summary.pathways}
                </Typography>
              </Box>
            )}
            {node.llm_summary.localization && (
              <Box sx={{ mb: 1.5 }}>
                <Typography variant="body2" sx={{ color: '#E8E8F0', lineHeight: 1.7, fontSize: '0.85rem' }}>
                  <strong style={{ color: '#BB8FCE' }}>Localization:</strong>{' '}
                  {node.llm_summary.localization}
                </Typography>
              </Box>
            )}
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
