import React from 'react';
import { FormControlLabel, Checkbox, Typography, Box, Chip } from '@mui/material';
import { useApp } from '../context/AppContext';
import { filterLogger } from '../utils/logger';

function FilterControls() {
  const { state, dispatch } = useApp();
  const logger = filterLogger;

  const interactionTypes = [
    { key: 'physical_binding', label: 'Physical Binding', icon: '🔗' },
    { key: 'regulatory', label: 'Regulatory', icon: '⚡' },
    { key: 'complex_formation', label: 'Complex Formation', icon: '🔗' },
    { key: 'genetic', label: 'Genetic', icon: '🧬' },
  ];

  const handleToggle = (key) => {
    const newState = !state.filters[key];
    logger.info(`Filter toggled: ${key} → ${newState}`);
    dispatch({ type: 'TOGGLE_FILTER', payload: key });
  };

  const activeFilters = Object.keys(state.filters).filter((k) => state.filters[k]);

  return (
    <Box
      sx={{
        mb: 3,
        p: 3,
        borderRadius: 3,
        background: 'rgba(26, 26, 46, 0.8)',
        backdropFilter: 'blur(20px)',
        border: '1px solid rgba(255,255,255,0.06)',
        boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
      }}
    >
      <Typography
        variant="subtitle2"
        sx={{
          color: '#E8E8F0',
          mb: 2.5,
          fontWeight: 600,
          fontSize: '0.8rem',
          textTransform: 'uppercase',
          letterSpacing: '0.08em',
        }}
      >
        Filter Interactions
      </Typography>

      {interactionTypes.map((type) => {
        const isActive = state.filters[type.key];
        return (
          <FormControlLabel
            key={type.key}
            control={
              <Checkbox
                checked={isActive}
                onChange={() => handleToggle(type.key)}
                size="small"
                sx={{
                  '& .MuiSvgIcon-root': {
                    fontSize: 18,
                  },
                  color: isActive ? '#6C63FF' : '#4A4A6A',
                  '&.Mui-checked': {
                    color: '#6C63FF',
                  },
                }}
              />
            }
            label={
              <Typography
                variant="body2"
                sx={{
                  color: isActive ? '#E8E8F0' : '#5A5A7A',
                  fontSize: '0.85rem',
                  fontWeight: isActive ? 500 : 400,
                  transition: 'color 0.2s ease',
                }}
              >
                {type.label}
              </Typography>
            }
            sx={{
              mb: 1,
              '& .MuiFormControlLabel-label': {
                ml: 1,
              },
            }}
          />
        );
      })}

      <Box sx={{ mt: 2.5, pt: 2, borderTop: '1px solid rgba(255,255,255,0.06)' }}>
        <Typography variant="caption" sx={{ color: '#A0A0B8', fontSize: '0.75rem' }}>
          Active:{' '}
          <Chip
            label={activeFilters.length === 0 ? 'All' : activeFilters.join(', ')}
            size="small"
            sx={{
              ml: 0.5,
              fontSize: '0.7rem',
              backgroundColor: 'rgba(108, 99, 255, 0.15)',
              border: '1px solid rgba(108, 99, 255, 0.3)',
              color: '#8B85FF',
              height: 20,
            }}
          />
        </Typography>
      </Box>

      {/* Graph depth display */}
      <Box sx={{ mt: 2 }}>
        <Typography variant="caption" sx={{ color: '#A0A0B8', fontSize: '0.75rem' }}>
          Graph Depth: <strong style={{ color: '#E8E8F0' }}>{state.depth}</strong> levels
        </Typography>
      </Box>
    </Box>
  );
}

export default FilterControls;
