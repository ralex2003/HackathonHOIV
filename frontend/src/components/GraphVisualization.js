import React, { useEffect, useRef } from 'react';
import { Network } from 'vis-network/standalone';
import { DataSet } from 'vis-data';
import { Box, IconButton, Tooltip } from '@mui/material';
import { ZoomIn, ZoomOut, Refresh, Download } from '@mui/icons-material';
import { useApp } from '../context/AppContext';

function GraphVisualization() {
  const { state, dispatch, setProteinDetailsOpen, setPaperListOpen } = useApp();
  const containerRef = useRef(null);
  const networkInstance = useRef(null);

  useEffect(() => {
    if (containerRef.current && state.graphData) {
      // Destroy existing network
      if (networkInstance.current) {
        networkInstance.current.destroy();
      }

      const options = {
        nodes: {
          shape: 'dot',
          size: 20,
          font: {
            size: 14,
            face: 'Arial',
          },
          borderWidth: 2,
        },
        edges: {
          width: 2,
          color: { inherit: 'from' },
          smooth: {
            type: 'continuous',
          },
          font: {
            size: 10,
            align: 'middle',
          },
        },
        physics: {
          stabilization: true,
          barnesHut: {
            gravitationalConstant: -2000,
            centralGravity: 0.3,
            springLength: 150,
            springConstant: 0.04,
          },
        },
        interaction: {
          hover: true,
          tooltipDelay: 200,
        },
      };

      const data = {
        nodes: new DataSet(state.graphData.nodes),
        edges: new DataSet(state.graphData.edges),
      };

      networkInstance.current = new Network(containerRef.current, data, options);

      // Handle node click
      networkInstance.current.on('click', (params) => {
        if (params.nodes.length > 0) {
          const nodeId = params.nodes[0];
          const node = state.graphData.nodes.find((n) => n.id === nodeId);
          if (node) {
            dispatch({ type: 'SET_SELECTED_NODE', payload: node });
            setProteinDetailsOpen(true);
          }
        }
      });

      // Handle edge click
      networkInstance.current.on('click', (params) => {
        if (params.edges.length > 0) {
          const edgeId = params.edges[0];
          const edge = state.graphData.edges.find((e) => 
            `${e.from}-${e.to}` === edgeId || `${e.to}-${e.from}` === edgeId
          );
          if (edge) {
            dispatch({ type: 'SET_SELECTED_EDGE', payload: edge });
            dispatch({ type: 'SET_PAPERS', payload: edge.papers });
            setPaperListOpen(true);
          }
        }
      });
    }
  }, [state.graphData, dispatch, setProteinDetailsOpen, setPaperListOpen]);

  const handleZoomIn = () => {
    if (networkInstance.current) {
      const scale = networkInstance.current.getScale();
      networkInstance.current.moveTo({ scale: scale * 1.2 });
    }
  };

  const handleZoomOut = () => {
    if (networkInstance.current) {
      const scale = networkInstance.current.getScale();
      networkInstance.current.moveTo({ scale: scale * 0.8 });
    }
  };

  const handleReset = () => {
    if (networkInstance.current) {
      networkInstance.current.fit();
    }
  };

  const handleExport = () => {
    if (networkInstance.current) {
      const canvas = containerRef.current.querySelector('canvas');
      const url = canvas.toDataURL('image/png');
      const link = document.createElement('a');
      link.download = 'protein-network.png';
      link.href = url;
      link.click();
    }
  };

  return (
    <Box sx={{ position: 'relative', height: '100%', width: '100%' }}>
      <div ref={containerRef} style={{ height: '100%', width: '100%' }} />
      
      <Box sx={{ position: 'absolute', top: 16, right: 16, display: 'flex', gap: 1 }}>
        <Tooltip title="Zoom In">
          <IconButton onClick={handleZoomIn} size="small">
            <ZoomIn />
          </IconButton>
        </Tooltip>
        <Tooltip title="Zoom Out">
          <IconButton onClick={handleZoomOut} size="small">
            <ZoomOut />
          </IconButton>
        </Tooltip>
        <Tooltip title="Reset View">
          <IconButton onClick={handleReset} size="small">
            <Refresh />
          </IconButton>
        </Tooltip>
        <Tooltip title="Export Image">
          <IconButton onClick={handleExport} size="small">
            <Download />
          </IconButton>
        </Tooltip>
      </Box>
    </Box>
  );
}

export default GraphVisualization;
