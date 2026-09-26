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
  Paper,
} from '@mui/material';
import { Close } from '@mui/icons-material';
import { useApp } from '../context/AppContext';
import { getPaper } from '../services/api';
import { paperLogger } from '../utils/logger';

function PaperListPanel() {
  const { state, dispatch, paperListOpen, setPaperListOpen } = useApp();
  const [paperDetails, setPaperDetails] = useState({});
  const logger = paperLogger;

  const handleClose = () => {
    logger.debug("Closing paper list panel");
    setPaperListOpen(false);
    dispatch({ type: 'SET_SELECTED_EDGE', payload: null });
    dispatch({ type: 'SET_PAPERS', payload: [] });
  };

  const loadPaperDetails = async (pmid) => {
    if (paperDetails[pmid]) return;

    logger.debug(`Loading paper details: PMID=${pmid}`);
    try {
      const paper = await getPaper(pmid);
      setPaperDetails((prev) => ({ ...prev, [pmid]: paper }));
      logger.info(`Paper loaded: ${paper.title?.slice(0, 50) || pmid}`);
    } catch (error) {
      logger.error(`Error loading paper PMID=${pmid}:`, error);
    }
  };

  useEffect(() => {
    if (state.papers.length > 0) {
      logger.info(`Loading details for ${state.papers.length} papers`);
      state.papers.forEach((pmid) => loadPaperDetails(pmid));
    }
  }, [state.papers]);

  if (!state.selectedEdge) return null;

  const edge = state.selectedEdge;
  const accentColor = '#6C63FF';

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
          background: 'rgba(26, 26, 46, 0.95)',
          backdropFilter: 'blur(20px)',
          borderRight: '1px solid rgba(255,255,255,0.06)',
          animation: 'slideInLeft 0.3s ease-out',
        },
      }}
    >
      <Box sx={{ p: 3, height: '100%', overflow: 'auto' }}>
        {/* Header */}
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
          <Typography variant="h5" sx={{ color: '#E8E8F0', fontWeight: 700 }}>
            📄 Related Papers
          </Typography>
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

        <Divider sx={{ mb: 2 }} />

        {/* Edge info */}
        <Paper
          sx={{
            p: 2,
            mb: 2,
            backgroundColor: 'rgba(108, 99, 255, 0.08)',
            border: '1px solid rgba(108, 99, 255, 0.15)',
            borderRadius: 2,
          }}
        >
          <Typography variant="body2" sx={{ color: '#E8E8F0', fontFamily: 'monospace', fontSize: '0.85rem' }}>
            {edge.from} → {edge.to}
          </Typography>
          <Chip
            label={`${edge.paper_count} paper${edge.paper_count !== 1 ? 's' : ''}`}
            size="small"
            sx={{
              mt: 1,
              backgroundColor: 'rgba(78, 205, 196, 0.15)',
              border: '1px solid rgba(78, 205, 196, 0.3)',
              color: '#4ECDC4',
              fontSize: '0.75rem',
            }}
          />
        </Paper>

        {/* Paper list */}
        <List sx={{ pt: 0 }}>
          {state.papers.map((pmid) => {
            const paper = paperDetails[pmid];
            return (
              <ListItem
                key={pmid}
                alignItems="flex-start"
                disablePadding
                sx={{
                  mb: 1.5,
                  borderRadius: 2,
                  border: '1px solid rgba(255,255,255,0.04)',
                  px: 1,
                  py: 1,
                  transition: 'all 0.2s ease',
                  '&:hover': {
                    borderColor: 'rgba(108, 99, 255, 0.2)',
                    backgroundColor: 'rgba(108, 99, 255, 0.05)',
                  },
                }}
              >
                <ListItemText
                  primary={
                    <Link
                      href={paper?.url || `https://pubmed.ncbi.nlm.nih.gov/${pmid}/`}
                      target="_blank"
                      rel="noopener noreferrer"
                      underline="none"
                      sx={{
                        color: '#8B85FF',
                        fontWeight: 500,
                        fontSize: '0.9rem',
                        lineHeight: 1.4,
                        '&:hover': {
                          color: '#6C63FF',
                          textDecoration: 'none',
                        },
                      }}
                    >
                      {paper?.title || `Paper ${pmid}`}
                    </Link>
                  }
                  secondary={
                    <Box sx={{ mt: 0.5 }}>
                      <Typography variant="caption" sx={{ color: '#A0A0B8', fontSize: '0.75rem' }}>
                        {paper?.year || 'Unknown year'}
                      </Typography>
                      {paper?.abstract && (
                        <Typography
                          variant="caption"
                          sx={{
                            display: 'block',
                            mt: 0.5,
                            color: '#6B6B80',
                            fontSize: '0.7rem',
                            lineHeight: 1.4,
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            display: '-webkit-box',
                            WebkitLineClamp: 2,
                            WebkitBoxOrient: 'vertical',
                          }}
                        >
                          {paper.abstract.slice(0, 120)}...
                        </Typography>
                      )}
                    </Box>
                  }
                  sx={{
                    '& .MuiListItemText-primary': {
                      fontSize: '0.9rem',
                    },
                  }}
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
