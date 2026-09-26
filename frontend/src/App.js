import React from 'react';
import { ThemeProvider, createTheme, CssBaseline, Box, Alert, AlertTitle, CircularProgress, Typography, useMediaQuery } from '@mui/material';
import { AppProvider, useApp } from './context/AppContext';
import SearchBar from './components/SearchBar';
import GraphVisualization from './components/GraphVisualization';
import ProteinDetailsPanel from './components/ProteinDetailsPanel';
import PaperListPanel from './components/PaperListPanel';
import FilterControls from './components/FilterControls';
import { appLogger } from './utils/logger';

const darkTheme = createTheme({
  palette: {
    mode: 'dark',
    primary: {
      main: '#6C63FF',
      light: '#8B85FF',
      dark: '#5A52D5',
      contrastText: '#FFFFFF',
    },
    secondary: {
      main: '#FF6584',
      light: '#FF859C',
      dark: '#E04D6A',
      contrastText: '#FFFFFF',
    },
    background: {
      default: '#0D0D1A',
      paper: '#1A1A2E',
    },
    surface: {
      main: '#16213E',
      light: '#1A2744',
      darker: '#0F172A',
    },
    text: {
      primary: '#E8E8F0',
      secondary: '#A0A0B8',
    },
    divider: '#2A2A4A',
    success: { main: '#4ADE80' },
    warning: { main: '#FBBF24' },
    error: { main: '#F87171' },
    info: { main: '#60A5FA' },
  },
  typography: {
    fontFamily: "'Inter', 'Segoe UI', 'Roboto', 'Helvetica', sans-serif",
    h4: {
      fontWeight: 700,
      letterSpacing: '-0.02em',
    },
    h6: {
      fontWeight: 600,
      letterSpacing: '-0.01em',
    },
    subtitle2: {
      fontWeight: 600,
      letterSpacing: '0.01em',
    },
    body2: {
      lineHeight: 1.6,
    },
  },
  shape: {
    borderRadius: 12,
  },
  components: {
    MuiPaper: {
      defaultProps: {
        elevation: 0,
        square: false,
      },
      styleOverrides: {
        root: {
          border: '1px solid rgba(255,255,255,0.06)',
          backdropFilter: 'blur(12px)',
        },
      },
    },
    MuiTextField: {
      styleOverrides: {
        root: {
          '& .MuiOutlinedInput-root': {
            borderRadius: 10,
            backgroundColor: '#0D0D1A',
            '&:hover fieldset': {
              borderColor: '#6C63FF',
            },
            '&.Mui-focused fieldset': {
              borderColor: '#6C63FF',
              borderWidth: 2,
            },
          },
        },
      },
    },
    MuiButton: {
      styleOverrides: {
        root: {
          borderRadius: 10,
          textTransform: 'none',
          fontWeight: 600,
          letterSpacing: '0.02em',
        },
        contained: {
          background: 'linear-gradient(135deg, #6C63FF 0%, #5A52D5 100%)',
          boxShadow: '0 4px 15px rgba(108, 99, 255, 0.4)',
          '&:hover': {
            background: 'linear-gradient(135deg, #7B73FF 0%, #6B63E5 100%)',
            boxShadow: '0 6px 20px rgba(108, 99, 255, 0.5)',
            transform: 'translateY(-1px)',
          },
        },
      },
    },
    MuiSlider: {
      styleOverrides: {
        root: {
          color: '#6C63FF',
          '& .MuiSlider-thumb': {
            width: 20,
            height: 20,
            border: '3px solid #6C63FF',
            backgroundColor: '#0D0D1A',
            boxShadow: '0 0 10px rgba(108, 99, 255, 0.5)',
          },
          '& .MuiSlider-track': {
            height: 4,
          },
          '& .MuiSlider-rail': {
            height: 4,
            color: '#2A2A4A',
          },
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: {
          borderRadius: 8,
          fontWeight: 500,
          border: '1px solid rgba(108, 99, 255, 0.3)',
        },
      },
    },
    MuiAlert: {
      styleOverrides: {
        root: {
          borderRadius: 12,
          border: 'none',
        },
      },
    },
    MuiDivider: {
      styleOverrides: {
        root: {
          borderColor: 'rgba(255,255,255,0.06)',
        },
      },
    },
    MuiIconButton: {
      styleOverrides: {
        root: {
          backgroundColor: 'rgba(108, 99, 255, 0.1)',
          border: '1px solid rgba(108, 99, 255, 0.2)',
          transition: 'all 0.2s ease',
          '&:hover': {
            backgroundColor: 'rgba(108, 99, 255, 0.25)',
            borderColor: '#6C63FF',
            transform: 'scale(1.1)',
          },
        },
      },
    },
  },
});

function AppContent() {
  const { state } = useApp();
  const logger = appLogger;
  const isDark = useMediaQuery('(prefers-color-scheme: dark)');

  React.useEffect(() => {
    if (state.loading) {
      logger.debug("Application state: loading");
    }
    if (state.error) {
      logger.error(`Application error: ${state.error}`);
    }
    if (state.graphData) {
      logger.info(`Graph updated: ${state.graphData.nodes?.length || 0} nodes`);
    }
  }, [state.loading, state.error, state.graphData, logger]);

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        height: '100vh',
        backgroundColor: '#0D0D1A',
        backgroundImage: `
          radial-gradient(ellipse at 20% 50%, rgba(108, 99, 255, 0.08) 0%, transparent 50%),
          radial-gradient(ellipse at 80% 80%, rgba(255, 101, 132, 0.05) 0%, transparent 50%)
        `,
      }}
    >
      <Box
        sx={{
          px: 4,
          py: 2,
          display: 'flex',
          alignItems: 'center',
          gap: 2,
          borderBottom: '1px solid rgba(255,255,255,0.06)',
          backgroundColor: 'rgba(13, 13, 26, 0.9)',
          backdropFilter: 'blur(20px)',
          position: 'sticky',
          top: 0,
          zIndex: 100,
        }}
      >
        <Box
          sx={{
            width: 36,
            height: 36,
            borderRadius: 10,
            background: 'linear-gradient(135deg, #6C63FF 0%, #FF6584 100%)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '18px',
            fontWeight: 700,
            color: '#fff',
            boxShadow: '0 4px 15px rgba(108, 99, 255, 0.4)',
          }}
        >
          🧬
        </Box>
        <Box>
          <Typography
            variant="h6"
            sx={{
              color: '#E8E8F0',
              fontWeight: 700,
              letterSpacing: '-0.02em',
              lineHeight: 1.2,
            }}
          >
            Protein Friend Finder
          </Typography>
          <Typography
            variant="caption"
            sx={{
              color: '#A0A0B8',
              fontSize: '0.7rem',
              letterSpacing: '0.05em',
              textTransform: 'uppercase',
            }}
          >
            Interaction Network Visualizer
          </Typography>
        </Box>
      </Box>

      <SearchBar />

      <Box sx={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        <Box
          sx={{
            width: 300,
            p: 3,
            overflow: 'auto',
            borderRight: '1px solid rgba(255,255,255,0.06)',
            backgroundColor: 'rgba(13, 13, 26, 0.5)',
          }}
        >
          <FilterControls />
        </Box>

        <Box
          sx={{
            flex: 1,
            position: 'relative',
            backgroundColor: '#0D0D1A',
          }}
        >
          {state.loading && (
            <Box
              sx={{
                position: 'absolute',
                top: '50%',
                left: '50%',
                transform: 'translate(-50%, -50%)',
                zIndex: 1000,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                gap: 2,
              }}
            >
              <Box
                sx={{
                  width: 48,
                  height: 48,
                  border: '3px solid rgba(108, 99, 255, 0.2)',
                  borderTopColor: '#6C63FF',
                  borderRadius: '50%',
                  animation: 'spin 1s linear infinite',
                }}
              />
              <Typography
                variant="body2"
                sx={{ color: '#A0A0B8', fontSize: '0.85rem' }}
              >
                Analyzing protein interactions...
              </Typography>
              <style>
                {`@keyframes spin { to { transform: rotate(360deg); } }`}
              </style>
            </Box>
          )}

          {state.error && (
            <Box sx={{ m: 3, maxWidth: 500 }}>
              <Alert
                severity="error"
                sx={{
                  backgroundColor: 'rgba(248, 113, 113, 0.1)',
                  border: '1px solid rgba(248, 113, 113, 0.3)',
                  borderRadius: 12,
                  color: '#F87171',
                }}
              >
                <AlertTitle sx={{ color: '#F87171', fontWeight: 600 }}>
                  Error
                </AlertTitle>
                {state.error}
              </Alert>
            </Box>
          )}

          {state.graphData && (
            <GraphVisualization />
          )}
            <Box
              sx={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                height: '100%',
                color: 'text.secondary',
                textAlign: 'center',
                px: 4,
              }}
            >
              <Box
                sx={{
                  fontSize: 64,
                  mb: 3,
                  opacity: 0.3,
                  filter: 'drop-shadow(0 0 30px rgba(108, 99, 255, 0.3))',
                }}
              >
                🧬
              </Box>
              <Typography
                variant="h5"
                sx={{
                  color: '#E8E8F0',
                  fontWeight: 600,
                  mb: 1,
                  letterSpacing: '-0.02em',
                }}
              >
                Search for a protein
              </Typography>
              <Typography
                variant="body1"
                sx={{
                  color: '#A0A0B8',
                  maxWidth: 400,
                  lineHeight: 1.6,
                }}
              >
                Enter a protein name or UniProt ID to visualize its
                interaction network
              </Typography>
              <Box
                sx={{
                  display: 'flex',
                  gap: 3,
                  mt: 4,
                }}
              >
                {['TP53', 'BRCA1', 'AKT1'].map((example) => (
                  <Box
                    key={example}
                    sx={{
                      px: 2,
                      py: 0.8,
                      borderRadius: 6,
                      backgroundColor: 'rgba(108, 99, 255, 0.1)',
                      border: '1px solid rgba(108, 99, 255, 0.2)',
                      fontSize: '0.8rem',
                      color: '#8B85FF',
                      fontWeight: 500,
                      fontFamily: 'monospace',
                    }}
                  >
                    {example}
                  </Box>
                ))}
              </Box>
            </Box>
          )}

          {state.graphData && (
            <GraphVisualization />
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
    <ThemeProvider theme={darkTheme}>
      <CssBaseline />
      <AppProvider>
        <AppContent />
      </AppProvider>
    </ThemeProvider>
  );
}

export default App;
