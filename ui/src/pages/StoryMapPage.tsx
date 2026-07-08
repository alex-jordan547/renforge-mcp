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

function StoryMapInner({ data, loading, error, onJump, currentLabel }: StoryMapPageProps) {
  const [layoutBusy, setLayoutBusy] = useState(false);
  const [warpBusy, setWarpBusy] = useState(false);
  const [warpTarget, setWarpTarget] = useState<string | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  const { zoomIn, zoomOut, fitView } = useReactFlow();

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
    return <section className="panel empty">Chargement de la Story Map…</section>;
  }

  if (error) {
    return <section className="panel empty">Impossible de charger la Story Map: {error}</section>;
  }

  if (!data.nodes.length) {
    return (
      <section className="panel empty">
        <h2>Story Map vide</h2>
        <p>Le backend n’a pas encore exposé les données.</p>
      </section>
    );
  }

  return (
    <div className="wrap">
      <div className="page-head reveal in">
        <h2>Story Map</h2>
        <span className="hint">
          {data.nodes.length} labels · {data.edges.length} transitions · clique un nœud pour relancer
        </span>
      </div>

      <div className="map-wrap reveal in" style={{ animationDelay: ".06s" }}>
        <div className="map-meta">
          <span style={{ color: "var(--muted)", fontSize: "12.5px" }}>Position actuelle</span>
          <span className="pill-label">
            <span className="dot" />
            {currentLabel || "—"}
          </span>
        </div>

        <div className="storyCanvas" aria-busy={warpBusy}>
          <ReactFlow
            onNodeClick={warpBusy ? undefined : onNodeClick}
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            fitView
            panOnDrag={true}
            zoomOnScroll={true}
            onlyRenderVisibleElements={true}
          >
            <Background gap={20} />
            <MiniMap pannable zoomable />
          </ReactFlow>

          <div className="zoom">
            <button id="zin" onClick={() => zoomIn()}>+</button>
            <button id="zout" onClick={() => zoomOut()}>−</button>
            <button id="zfit" title="Ajuster" onClick={() => fitView()}>⊡</button>
          </div>
        </div>
      </div>

      {warpBusy ? (
        <div className="statusLine">Redémarrage du jeu sur « {warpTarget} »…</div>
      ) : layoutBusy ? (
        <div className="statusLine">Ajustement du graphe…</div>
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
