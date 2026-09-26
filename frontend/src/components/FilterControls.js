import React from 'react';
import { FormControlLabel, Checkbox, Typography, Paper } from '@mui/material';
import { useApp } from '../context/AppContext';

function FilterControls() {
  const { state, dispatch } = useApp();

  const interactionTypes = [
    { key: 'physical_binding', label: 'Physical Binding' },
    { key: 'regulatory', label: 'Regulatory' },
    { key: 'complex_formation', label: 'Complex Formation' },
    { key: 'genetic', label: 'Genetic' },
  ];

  const handleToggle = (key) => {
    dispatch({ type: 'TOGGLE_FILTER', payload: key });
  };

  return (
    <Paper sx={{ p: 2, mb: 2 }}>
      <Typography variant="subtitle2" gutterBottom>
        Filter by Interaction Type
      </Typography>
      {interactionTypes.map((type) => (
        <FormControlLabel
          key={type.key}
          control={
            <Checkbox
              checked={state.filters[type.key]}
              onChange={() => handleToggle(type.key)}
              size="small"
            />
          }
          label={type.label}
        />
      ))}
    </Paper>
  );
}

export default FilterControls;
