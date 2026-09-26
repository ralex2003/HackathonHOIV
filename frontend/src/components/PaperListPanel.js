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
      const unloadedPapers = state.papers.filter((pmid) => !paperDetails[pmid]);
      unloadedPapers.forEach((pmid) => loadPaperDetails(pmid));
    }
  }, [state.papers]); // eslint-disable-line react-hooks/exhaustive-deps

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
