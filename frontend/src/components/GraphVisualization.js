import React, { useCallback, useEffect, useMemo, useRef } from 'react';
import { Network } from 'vis-network/standalone';
import { DataSet } from 'vis-data';
import { Box, IconButton, Tooltip, Typography } from '@mui/material';
import {
  ZoomIn, ZoomOut, Refresh, Download,
} from '@mui/icons-material';
import { useApp } from '../context/AppContext';
import { graphLogger } from '../utils/logger';

// Color palette for nodes by level
const LEVEL_COLORS = [
  '#6C63FF', // purple - center
  '#4ECDC4', // teal
  '#F7DC6F', // gold
  '#BB8FCE', // lavender
  '#85C1E9', // sky blue
  '#F0B27A', // peach
];

// Node sizes by level
const LEVEL_SIZES = [36, 26, 20, 16, 14, 12];

function dedupeById(items, key = 'id') {
  const seen = new Map();
  items.forEach((item) => {
    const id = item[key];
    if (id === undefined || id === null) return;
    if (seen.has(id)) {
      seen.set(id, { ...seen.get(id), ...item });
    } else {
      seen.set(id, item);
    }
  });
  return Array.from(seen.values());
}

function GraphVisualization() {
  const { state, dispatch, setProteinDetailsOpen, setPaperListOpen } = useApp();
  const containerRef = useRef(null);
  const networkInstance = useRef(null);
  const logger = graphLogger;

  const nodes = useMemo(
    () => (state.graphData?.nodes ? dedupeById(state.graphData.nodes) : []),
    [state.graphData]
  );
  const edges = useMemo(
    () => (state.graphData?.edges ? dedupeById(state.graphData.edges) : []),
    [state.graphData]
  );

  const nodeById = useMemo(() => {
    const map = new Map();
    nodes.forEach((node) => map.set(node.id, node));
    return map;
  }, [nodes]);

  const edgeById = useMemo(() => {
    const map = new Map();
    edges.forEach((edge) => map.set(edge.id, edge));
    return map;
  }, [edges]);

  useEffect(() => {
    if (state.graphData) {
      logger.info(`Graph data received: ${nodes.length} nodes, ${edges.length} edges`);
    }
  }, [state.graphData, nodes.length, edges.length, logger]);

  // The protein panel and the connection panel are mutually exclusive. Both are
  // MUI Modals, so if two are open at once the later-mounted one (the
  // connection panel) stacks its backdrop on top of the protein panel and
  // traps focus -- the protein description becomes invisible. Each handler
  // therefore tears the other one down.
  const handleNodeSelect = useCallback(
    (nodeId) => {
      const node = nodeById.get(nodeId);
      if (!node) return;

      logger.debug(`Node selected: ${node.label || node.id}`);
      dispatch({ type: 'SET_SELECTED_NODE', payload: node });
      dispatch({ type: 'SET_SELECTED_EDGE', payload: null });
      dispatch({ type: 'SET_PAPERS', payload: [] });
      setPaperListOpen(false);
      setProteinDetailsOpen(true);
    },
    [dispatch, nodeById, setPaperListOpen, setProteinDetailsOpen, logger]
  );

  const handleEdgeSelect = useCallback(
    (edgeId) => {
      const edge = edgeById.get(edgeId);
      if (!edge) return;

      logger.debug(`Edge selected: ${edge.from} → ${edge.to} (${edge.label})`);
      dispatch({ type: 'SET_SELECTED_EDGE', payload: edge });
      dispatch({ type: 'SET_PAPERS', payload: edge.papers || [] });
      dispatch({ type: 'SET_SELECTED_NODE', payload: null });
      setProteinDetailsOpen(false);
      setPaperListOpen(true);
    },
    [dispatch, edgeById, setProteinDetailsOpen, setPaperListOpen, logger]
  );

  useEffect(() => {
    if (!containerRef.current || !state.graphData) return undefined;

    logger.info("Initializing vis-network visualization...");

    if (networkInstance.current) {
      networkInstance.current.destroy();
      networkInstance.current = null;
    }

    // Build styled nodes with rich HTML labels
    const styledNodes = nodes.map((node) => {
      const level = node.level || 0;
      const color = LEVEL_COLORS[Math.min(level, LEVEL_COLORS.length - 1)];
      const size = LEVEL_SIZES[Math.min(level, LEVEL_SIZES.length - 1)];

      return {
        ...node,
        color: {
          background: color,
          border: color,
          highlight: {
            background: '#FFFFFF',
            border: color,
            opacity: 0.9,
          },
          hover: {
            background: color,
            border: '#FFFFFF',
            opacity: 0.8,
          },
        },
        borderWidth: 3,
        shadow: true,
        shadowColor: `${color}80`,
        shadowSize: 15,
        shadowX: 0,
        shadowY: 5,
        font: {
          color: '#E8E8F0',
          size: level === 0 ? 16 : 13,
          face: 'Inter, Segoe UI, sans-serif',
          align: 'center',
          strokeWidth: 3,
          strokeColor: '#0D0D1A',
          bold: {
            color: '#FFFFFF',
            size: level === 0 ? 17 : 14,
            face: 'Inter, Segoe UI, sans-serif',
            strokeWidth: 3,
            strokeColor: '#0D0D1A',
          },
        },
        shape: 'dot',
        size: size,
        scaling: {
          min: 10,
          max: 50,
          label: true,
        },
      };
    });

    // Build styled edges
    const styledEdges = edges.map((edge) => {
      const isSkipping = edge.level_skipping;
      return {
        ...edge,
        color: {
          color: isSkipping ? 'rgba(255, 165, 0, 0.4)' : 'rgba(108, 99, 255, 0.4)',
          highlight: isSkipping ? '#FFA500' : '#6C63FF',
          hover: isSkipping ? '#FFB833' : '#8B85FF',
          opacity: 0.6,
        },
        width: isSkipping ? 3 : 2,
        dashed: isSkipping,
        smooth: {
          type: 'cubicBezier',
          roundness: 0.35,
        },
        font: {
          color: isSkipping ? '#FFA500' : '#A0A0B8',
          size: 10,
          face: 'Inter, sans-serif',
          align: 'middle',
          background: 'rgba(13, 13, 26, 0.8)',
          strokeWidth: 2,
          strokeColor: '#0D0D1A',
        },
        arrows: {
          to: {
            enabled: true,
            scaleFactor: 0.6,
            type: 'arrow',
          },
        },
        selectionWidth: 3,
        hoverWidth: 3,
      };
    });

    const options = {
      nodes: {
        shape: 'dot',
        borderWidth: 3,
        shadow: true,
      },
      edges: {
        color: {
          color: 'rgba(108, 99, 255, 0.4)',
          highlight: '#6C63FF',
          hover: '#8B85FF',
        },
        width: 2,
        smooth: { type: 'cubicBezier', roundness: 0.35 },
        font: {
          color: '#A0A0B8',
          size: 10,
          face: 'Inter, sans-serif',
          align: 'middle',
          background: 'rgba(13, 13, 26, 0.8)',
          strokeWidth: 2,
          strokeColor: '#0D0D1A',
        },
        arrows: { to: { enabled: true, scaleFactor: 0.6 } },
        hoverWidth: 3,
        selectionWidth: 3,
      },
      layout: {
        hierarchical: {
          enabled: true,
          direction: 'DU',           // down: root at top, children below
          sortMethod: 'directed',    // respect edge direction for level assignment
          levelSeparation: 160,      // vertical gap between levels
          nodeSpacing: 120,          // horizontal gap between nodes in same level
          treeSpacing: 250,          // gap between subtrees
          blockShifting: true,       // shift blocks to minimize edge crossings
          edgeMinimization: true,    // minimize edge crossings
          shakeTowards: 'roots',     // keep roots near the top
          parentCentralization: true,
        }
      },
      physics: {
        enabled: false,   // disable force-directed; hierarchical handles positioning
      },
      interaction: {
        hover: true,
        tooltipDelay: 200,
        navigationButtons: false,
        keyboard: {
          enabled: true,
          speed: { x: 10, y: 10, zoom: 0.02 },
          filter: function() { return true; },
        },
        dragNodes: true,
        dragView: true,
        zoomView: true,
        selectConnectedEdges: false,
      },
      groups: {},
      configure: {
        enabled: false,
      },
    };

    // Custom tooltip
    Network.configure = function() {};

    const network = new Network(
      containerRef.current,
      { nodes: new DataSet(styledNodes), edges: new DataSet(styledEdges) },
      options
    );
    networkInstance.current = network;
    // selectNode/selectEdge are mutually exclusive by construction. The
    // generic 'click' event is not: its payload can carry both nodes and edges
    // when a click lands near an edge, which is how a protein click ended up
    // opening the connection summary.
    network.on('selectNode', (params) => handleNodeSelect(params.nodes[0]));
    network.on('selectEdge', (params) => handleEdgeSelect(params.edges[0]));

    // Configure tooltips via CSS
    const style = document.createElement('style');
    style.textContent = `
      .vis-tooltip {
        background: rgba(26, 26, 46, 0.95) !important;
        color: #E8E8F0 !important;
        border: 1px solid rgba(108, 99, 255, 0.3) !important;
        border-radius: 10px !important;
        padding: 10px 14px !important;
        font-family: Inter, sans-serif !important;
        font-size: 12px !important;
        box-shadow: 0 8px 32px rgba(0,0,0,0.5) !important;
        backdrop-filter: blur(10px) !important;
        max-width: 280px !important;
      }
      .vis-tooltip .vis-network-caption {
        font-weight: 600 !important;
        color: #8B85FF !important;
        font-size: 13px !important;
        margin-bottom: 4px !important;
      }
      .vis-network-edgelabel {
        color: #A0A0B8 !important;
        font-family: Inter, sans-serif !important;
      }
      .vis-selected {
        border-width: 4px !important;
      }
    `;
    document.head.appendChild(style);

    logger.info(`Network initialized: ${nodes.length} nodes, ${edges.length} edges rendered`);

    return () => {
      network.destroy();
      if (networkInstance.current === network) {
        networkInstance.current = null;
      }
      logger.debug("Network instance destroyed");
    };
  }, [state.graphData, nodes, edges, handleNodeSelect, handleEdgeSelect, logger]);

  const handleZoomIn = () => {
    if (networkInstance.current) {
      const scale = networkInstance.current.getScale();
      networkInstance.current.moveTo({ scale: scale * 1.2 });
      logger.debug("Zoom in to scale:", scale * 1.2);
    }
  };

  const handleZoomOut = () => {
    if (networkInstance.current) {
      const scale = networkInstance.current.getScale();
      networkInstance.current.moveTo({ scale: scale * 0.8 });
      logger.debug("Zoom out to scale:", scale * 0.8);
    }
  };

  const handleReset = () => {
    if (networkInstance.current) {
      networkInstance.current.fit();
      logger.debug("View reset to fit");
    }
  };

  const handleExport = () => {
    if (!containerRef.current) return;
    const canvas = containerRef.current.querySelector('canvas');
    if (!canvas) return;
    try {
      logger.info("Exporting graph as PNG...");
      const url = canvas.toDataURL('image/png');
      const link = document.createElement('a');
      link.download = 'protein-network.png';
      link.href = url;
      link.click();
      logger.info("Graph exported successfully");
    } catch (err) {
      logger.error('Export failed:', err);
    }
  };

  return (
    <Box sx={{ position: 'relative', height: '100%', width: '100%' }}>
      <div ref={containerRef} style={{ height: '100%', width: '100%' }} />

      <Box
        sx={{
          position: 'absolute',
          top: 16,
          right: 16,
          display: 'flex',
          gap: 1,
          zIndex: 10,
        }}
      >
        <Tooltip title="Zoom In" arrow>
          <IconButton onClick={handleZoomIn} size="small">
            <ZoomIn sx={{ fontSize: 18 }} />
          </IconButton>
        </Tooltip>
        <Tooltip title="Zoom Out" arrow>
          <IconButton onClick={handleZoomOut} size="small">
            <ZoomOut sx={{ fontSize: 18 }} />
          </IconButton>
        </Tooltip>
        <Tooltip title="Reset View" arrow>
          <IconButton onClick={handleReset} size="small">
            <Refresh sx={{ fontSize: 18 }} />
          </IconButton>
        </Tooltip>
        <Tooltip title="Export as PNG" arrow>
          <IconButton onClick={handleExport} size="small">
            <Download sx={{ fontSize: 18 }} />
          </IconButton>
        </Tooltip>
      </Box>

      {/* Legend */}
      <Box
        sx={{
          position: 'absolute',
          bottom: 16,
          left: 16,
          display: 'flex',
          gap: 2,
          alignItems: 'center',
          px: 2.5,
          py: 1.5,
          borderRadius: 3,
          backgroundColor: 'rgba(26, 26, 46, 0.9)',
          backdropFilter: 'blur(12px)',
          border: '1px solid rgba(255,255,255,0.06)',
          zIndex: 10,
        }}
      >
        <Typography variant="caption" sx={{ color: '#A0A0B8', fontWeight: 500, mr: 1 }}>
          Depth:
        </Typography>
        {LEVEL_COLORS.slice(0, 5).map((color, i) => (
          <Box key={i} sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
            <Box
              sx={{
                width: 10,
                height: 10,
                borderRadius: '50%',
                backgroundColor: color,
                boxShadow: `0 0 6px ${color}80`,
              }}
            />
            <Typography variant="caption" sx={{ color: '#A0A0B8', fontSize: '0.65rem' }}>
              {i}
            </Typography>
          </Box>
        ))}
        {/* Level-skipping edge indicator */}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, ml: 2 }}>
          <Box
            sx={{
              width: 20,
              height: 0,
              borderTop: '3px dashed #FFA500',
            }}
          />
          <Typography variant="caption" sx={{ color: '#A0A0B8', fontSize: '0.65rem' }}>
            long-range
          </Typography>
        </Box>
      </Box>
    </Box>
  );
}

export default GraphVisualization;
