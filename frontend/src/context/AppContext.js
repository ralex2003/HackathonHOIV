import React, { createContext, useContext, useReducer, useState } from 'react';
import { contextLogger } from '../utils/logger';

const AppContext = createContext();

const initialState = {
  currentProtein: null,
  graphData: null,
  selectedNode: null,
  selectedEdge: null,
  papers: [],
  loading: false,
  error: null,
  filters: {
    physical_binding: true,
    regulatory: true,
    complex_formation: true,
    genetic: true,
  },
  depth: 2,
};

function appReducer(state, action) {
  contextLogger.debug(`Action: ${action.type}`);
  switch (action.type) {
    case 'SET_CURRENT_PROTEIN':
      return { ...state, currentProtein: action.payload };
    case 'SET_GRAPH_DATA':
      return { ...state, graphData: action.payload };
    case 'SET_SELECTED_NODE':
      return { ...state, selectedNode: action.payload };
    case 'SET_SELECTED_EDGE':
      return { ...state, selectedEdge: action.payload };
    case 'SET_PAPERS':
      return { ...state, papers: action.payload };
    case 'SET_LOADING':
      return { ...state, loading: action.payload };
    case 'SET_ERROR':
      return { ...state, error: action.payload };
    case 'TOGGLE_FILTER':
      return {
        ...state,
        filters: {
          ...state.filters,
          [action.payload]: !state.filters[action.payload],
        },
      };
    case 'SET_DEPTH':
      return { ...state, depth: action.payload };
    case 'RESET':
      return initialState;
    default:
      return state;
  }
}

export function AppProvider({ children }) {
  const [state, dispatch] = useReducer(appReducer, initialState);
  const [proteinDetailsOpen, setProteinDetailsOpen] = useState(false);
  const [paperListOpen, setPaperListOpen] = useState(false);

  const value = {
    state,
    dispatch,
    proteinDetailsOpen,
    setProteinDetailsOpen,
    paperListOpen,
    setPaperListOpen,
  };

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp() {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error('useApp must be used within AppProvider');
  }
  return context;
}
