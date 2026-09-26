import React from 'react';
import { ThemeProvider, createTheme, CssBaseline, Box, Alert, CircularProgress, Typography } from '@mui/material';
import { AppProvider, useApp } from './context/AppContext';
import SearchBar from './components/SearchBar';
import GraphVisualization from './components/GraphVisualization';
import ProteinDetailsPanel from './components/ProteinDetailsPanel';
import PaperListPanel from './components/PaperListPanel';
import FilterControls from './components/FilterControls';

const theme = createTheme({
  palette: {
    primary: {
      main: '#1976d2',
    },
    secondary: {
      main: '#dc004e',
    },
  },
});

function AppContent() {
  const { state } = useApp();

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      <SearchBar />
      
      <Box sx={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        <Box sx={{ width: 300, p: 2, overflow: 'auto' }}>
          <FilterControls />
        </Box>
        
        <Box sx={{ flex: 1, position: 'relative' }}>
          {state.loading && (
            <Box
              sx={{
                position: 'absolute',
                top: '50%',
                left: '50%',
                transform: 'translate(-50%, -50%)',
                zIndex: 1000,
              }}
            >
              <CircularProgress />
            </Box>
          )}
          
          {state.error && (
            <Alert severity="error" sx={{ m: 2 }}>
              {state.error}
            </Alert>
          )}
          
          {state.graphData ? (
            <GraphVisualization />
          ) : (
            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                height: '100%',
                color: 'text.secondary',
              }}
            >
              <Typography variant="h6">
                Search for a protein to visualize its interaction network
              </Typography>
            </Box>
          )}
        </Box>
      </Box>
      
      <ProteinDetailsPanel />
      <PaperListPanel />
    </Box>
  );
}

function App() {
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <AppProvider>
        <AppContent />
      </AppProvider>
    </ThemeProvider>
  );
}

export default App;
