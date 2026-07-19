import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ReactFlow,
  Background,
  MiniMap,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
  type NodeMouseHandler,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import ELK from "elkjs/lib/elk.bundled.js";
import type { StoryMapResponse } from "../types";

interface StoryMapPageProps {
  data: StoryMapResponse;
  loading: boolean;
  error: string | null;
  onJump: (target: string) => void | Promise<void>;
  currentLabel?: string | null;
}

function mapNodeClass(type: string) {
  switch (type) {
    case "menu":
      return "node-menu";
    case "choice":
      return "node-choice";
    case "call":
      return "node-call";
    case "jump":
      return "node-jump";
    default:
      return "node-label";
  }
}

function focusClass(nodeId: string, focusedNodeId: string | null, focusedNodeIds: Set<string> | null) {
  if (!focusedNodeId || !focusedNodeIds) {
    return "";
  }
  if (nodeId === focusedNodeId) {
    return " focus-node";
  }
  return focusedNodeIds.has(nodeId) ? " focus-related" : " focus-muted";
}

function edgeEndpointNodeId(endpoint: string) {
  return endpoint.startsWith("label:") ? endpoint : `label:${endpoint}`;
}

function edgeTouchesNode(edge: Pick<StoryMapResponse["edges"][number], "source" | "target">, nodeId: string) {
  const normalizedNodeId = nodeId.replace(/^label:/, "");
  return [edge.source, edge.target].some(
    (endpoint) => endpoint === nodeId || endpoint === normalizedNodeId || edgeEndpointNodeId(endpoint) === nodeId,
  );
}

function StoryMapInner({ data, loading, error, onJump, currentLabel }: StoryMapPageProps) {
  const [layoutBusy, setLayoutBusy] = useState(false);
  const [warpBusy, setWarpBusy] = useState(false);
  const [warpTarget, setWarpTarget] = useState<string | null>(null);
  const [showEdgeLabels, setShowEdgeLabels] = useState(false);
  const [focusedNodeId, setFocusedNodeId] = useState<string | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  const { zoomIn, zoomOut, fitView } = useReactFlow();

  const focusedNodeIds = useMemo(() => {
    if (!focusedNodeId) {
      return null;
    }
    const related = new Set([focusedNodeId]);
    data.edges.forEach((edge) => {
      if (edgeTouchesNode(edge, focusedNodeId)) {
        related.add(edgeEndpointNodeId(edge.source));
        related.add(edgeEndpointNodeId(edge.target));
      }
    });
    return related;
  }, [data.edges, focusedNodeId]);

  const fallbackNodes = useMemo(
    () =>
      data.nodes.map((node, index) => {
        const { label, ...nodeData } = node.data;
        const isCurrent = node.id === currentLabel || node.data.name === currentLabel;
        const nodeType = isCurrent ? "current" : (node.data.type ?? "label");
        return {
          id: node.id,
          data: {
            ...nodeData,
            label: (
              <>
                {label}
                <span className="tag">{nodeType}</span>
              </>
            ),
          },
          position: {
            x: (index % 5) * 220 + (index % 2) * 10,
            y: Math.floor(index / 5) * 170,
          },
          className: `${mapNodeClass(node.data.type ?? "label")}${isCurrent ? " current" : ""}`,
        };
      }),
    [data.nodes, currentLabel],
  );

  const fallbackEdges = useMemo(
    () =>
      data.edges.map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        label: edge.label,
        type: "default",
      })),
    [data.edges],
  );

  useEffect(() => {
    let active = true;
    const run = async () => {
      setLayoutBusy(true);
      if (!data.nodes.length) {
        setNodes([]);
        setEdges([]);
        setLayoutBusy(false);
        return;
      }

      try {
        const elk = new ELK();
        const graph: {
          id: string;
          layoutOptions: Record<string, string>;
          children: Array<{ id: string; width: number; height: number; x?: number; y?: number }>;
          edges: Array<{ id: string; sources: string[]; targets: string[] }>;
        } = {
          id: "story-map-root",
          layoutOptions: {
            "elk.algorithm": "layered",
            "elk.direction": "RIGHT",
            "elk.padding": "[left=20,right=20,top=20,bottom=20]",
            "elk.spacing.nodeNodeBetweenLayers": "44",
          },
          children: data.nodes.map((node) => ({
            id: node.id,
            width: 220,
            height: 90,
            x: node.x,
            y: node.y,
          })),
          edges: data.edges.map((edge) => ({
            id: edge.id,
            sources: [edge.source],
            targets: [edge.target],
          })),
        };
        const positioned = await elk.layout(graph);

        const byId = new Map(
          (positioned.children ?? []).map((child: { id: string; x?: number; y?: number }) => [
            String(child.id),
            { x: child.x ?? 0, y: child.y ?? 0 },
          ]),
        );

        setNodes(
          data.nodes.map((node) => {
            const pos = byId.get(node.id);
            const { label, ...nodeData } = node.data;
            const isCurrent = node.id === currentLabel || node.data.name === currentLabel;
            const nodeType = isCurrent ? "current" : (node.data.type ?? "label");
            return {
              id: node.id,
              data: {
                ...nodeData,
                label: (
                  <>
                    {label}
                    <span className="tag">{nodeType}</span>
                  </>
                ),
              },
              position: pos || { x: 0, y: 0 },
              style: {
                width: 210,
              },
              className: `${mapNodeClass(node.data.type ?? "label")}${isCurrent ? " current" : ""}`,
            };
          }),
        );
        setEdges(
          data.edges.map((edge) => ({
            id: edge.id,
            source: edge.source,
            target: edge.target,
            label: edge.label,
            data: {
              kind: edge.type,
            },
            type: "default",
            style: { strokeWidth: 1.6 },
          })),
        );
      } catch (_layoutError) {
        setNodes(fallbackNodes);
        setEdges(fallbackEdges);
      } finally {
        if (active) {
          setLayoutBusy(false);
        }
      }
    };

    run();
    return () => {
      active = false;
    };
  }, [data.edges, data.nodes, setEdges, setNodes, fallbackEdges, fallbackNodes, currentLabel]);

  useEffect(() => {
    setNodes((currentNodes) =>
      currentNodes.map((node) => ({
        ...node,
        className: `${(node.className ?? "").replace(/\s+focus-(?:node|related|muted)/g, "")}${focusClass(node.id, focusedNodeId, focusedNodeIds)}`,
      })),
    );
  }, [focusedNodeId, focusedNodeIds, setNodes]);

  useEffect(() => {
    setEdges((currentEdges) =>
      currentEdges.map((edge) => {
        const active = Boolean(focusedNodeId && edgeTouchesNode(edge, focusedNodeId));
        return {
          ...edge,
          className: active ? "focus-edge" : undefined,
          domAttributes: focusedNodeId
            ? ({ "data-focus-edge": active ? "active" : "muted" } as Edge["domAttributes"])
            : undefined,
        };
      }),
    );
  }, [focusedNodeId, setEdges]);

  const onNodeClick: NodeMouseHandler = useCallback(
    async (_event, node) => {
      if (warpBusy) {
        return;
      }
      const target = String(node.data.name ?? node.id).replace(/^label:/, "");
      setWarpBusy(true);
      setWarpTarget(target);
      try {
        await onJump(target);
      } finally {
        setWarpBusy(false);
        setWarpTarget(null);
      }
    },
    [warpBusy, onJump],
  );

  if (loading) {
    return <section className="panel empty">Loading Story Map…</section>;
  }

  if (error) {
    return <section className="panel empty">Could not load Story Map: {error}</section>;
  }

  if (!data.nodes.length) {
    return (
      <section className="panel empty">
        <img className="emptyState-mascot" src="/brand/renforge-mascot.png" alt="" aria-hidden="true" />
        <h2>Empty Story Map</h2>
        <p>The backend has not exposed data yet.</p>
      </section>
    );
  }

  return (
    <div className="wrap">
      <div className="page-head reveal in">
        <h2>Story Map</h2>
        <span className="hint">
        {data.nodes.length} labels · {data.edges.length} transitions · hover to focus, click to replay
        </span>
      </div>

      <div className="map-wrap reveal in" style={{ animationDelay: ".06s" }}>
        <div className="map-meta">
          <div className="map-current">
            <span style={{ color: "var(--muted)", fontSize: "12.5px" }}>Current position</span>
            <span className="pill-label">
              <span className="dot" />
              {currentLabel || "—"}
            </span>
          </div>
          <div className="map-actions">
            <button
              className={`map-toggle ${showEdgeLabels ? "on" : ""}`}
              type="button"
              aria-pressed={showEdgeLabels}
              onClick={() => setShowEdgeLabels((visible) => !visible)}
            >
              <span className="map-toggle-indicator" aria-hidden="true" />
              {showEdgeLabels ? "Hide transitions" : "Show transitions"}
            </button>
          </div>
        </div>

        <div className={`storyCanvas${showEdgeLabels ? " show-edge-labels" : ""}`} aria-busy={warpBusy}>
          <ReactFlow
            onNodeClick={warpBusy ? undefined : onNodeClick}
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeMouseEnter={(_event, node) => setFocusedNodeId(node.id)}
            onNodeMouseLeave={() => setFocusedNodeId(null)}
            fitView
            panOnDrag={true}
            zoomOnScroll={true}
            onlyRenderVisibleElements={true}
          >
            <Background gap={20} />
            <MiniMap
              className="story-minimap"
              pannable
              zoomable
              nodeColor={(node) => {
                const type = String(node.data?.type ?? "label");
                const name = String(node.data?.name ?? node.id).replace(/^label:/, "");
                if (currentLabel && (name === currentLabel || node.id === currentLabel || node.id === `label:${currentLabel}`)) {
                  return "var(--minimap-node-current)";
                }
                if (type === "menu" || type === "choice") {
                  return "var(--minimap-node-menu)";
                }
                if (type === "call") {
                  return "var(--minimap-node-call)";
                }
                if (node.data?.covered) {
                  return "var(--minimap-node-covered)";
                }
                return "var(--minimap-node)";
              }}
              nodeStrokeColor={(node) => {
                const name = String(node.data?.name ?? node.id).replace(/^label:/, "");
                if (currentLabel && (name === currentLabel || node.id === currentLabel || node.id === `label:${currentLabel}`)) {
                  return "var(--minimap-node-current)";
                }
                return "var(--minimap-node-stroke)";
              }}
              nodeStrokeWidth={1}
              nodeBorderRadius={2}
              maskColor="var(--minimap-mask)"
              maskStrokeColor="var(--minimap-mask-stroke)"
              maskStrokeWidth={1.5}
              bgColor="var(--minimap-bg)"
              ariaLabel="Story map overview"
            />
          </ReactFlow>

          <div className="zoom">
            <button id="zin" onClick={() => zoomIn()}>+</button>
            <button id="zout" onClick={() => zoomOut()}>−</button>
            <button id="zfit" title="Fit" onClick={() => fitView()}>⊡</button>
          </div>
        </div>
      </div>

      {warpBusy ? (
        <div className="statusLine">Restarting game on "{warpTarget}"…</div>
      ) : layoutBusy ? (
        <div className="statusLine">Adjusting graph…</div>
      ) : null}
    </div>
  );
}

export function StoryMapPage(props: StoryMapPageProps) {
  return (
    <ReactFlowProvider>
      <StoryMapInner {...props} />
    </ReactFlowProvider>
  );
}
