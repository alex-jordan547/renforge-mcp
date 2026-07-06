import "@xyflow/react/dist/style.css";
import { useEffect, useMemo, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
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
  onJump: (target: string) => void;
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

export function StoryMapPage({ data, loading, error, onJump }: StoryMapPageProps) {
  const [layoutBusy, setLayoutBusy] = useState(false);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  const fallbackNodes = useMemo(
    () =>
      data.nodes.map((node, index) => {
        const { label, ...nodeData } = node.data;
        return {
          id: node.id,
          data: {
            ...nodeData,
            label,
          },
          position: {
            x: (index % 5) * 220 + (index % 2) * 10,
            y: Math.floor(index / 5) * 170,
          },
          className: mapNodeClass(node.data.type ?? "label"),
        };
      }),
    [data.nodes],
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
            return {
              id: node.id,
              data: {
                ...nodeData,
                label,
              },
              position: pos || { x: 0, y: 0 },
              style: {
                width: 210,
              },
              className: mapNodeClass(node.data.type ?? "label"),
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
  }, [data.edges, data.nodes, setEdges, setNodes, fallbackEdges, fallbackNodes]);

  const onNodeClick: NodeMouseHandler = (_event, node) => {
    const target = String(node.data.name ?? node.id);
    onJump(target.replace(/^label:/, ""));
  };

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
    <section className="panel storyMapPanel">
      <div className="panelHeader">
        <h2>Story Map</h2>
        <span>{data.nodes.length} labels • {data.edges.length} transitions</span>
      </div>
      <div className="storyCanvas">
        <ReactFlow
          onNodeClick={onNodeClick}
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          fitView
          panOnDrag={true}
          zoomOnScroll={true}
          onlyRenderVisibleElements={true}
        >
          <Background />
          <MiniMap pannable />
          <Controls />
        </ReactFlow>
      </div>
      {layoutBusy ? <div className="statusLine">Ajustement du graphe…</div> : null}
    </section>
  );
}
