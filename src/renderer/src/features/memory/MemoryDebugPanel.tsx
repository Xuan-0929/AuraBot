import type { ReactNode } from "react";
import type { MemoryDebugSnapshot } from "../../../../shared/contracts/ipc";

export interface MemoryDebugPanelProps {
  snapshot?: MemoryDebugSnapshot;
  expanded: boolean;
  onToggle(): void;
  onRefresh(): Promise<void>;
}

export function MemoryDebugPanel({ snapshot, expanded, onToggle, onRefresh }: MemoryDebugPanelProps) {
  return (
    <section className="memory-debug-shell" aria-label="Memory debug tools">
      <div className="memory-debug-toolbar">
        <button
          type="button"
          className="memory-debug-toggle"
          aria-expanded={expanded}
          onClick={() => {
            onToggle();
            if (!expanded) {
              void onRefresh();
            }
          }}
        >
          Memory Debug
        </button>
        {expanded ? (
          <button type="button" className="memory-debug-refresh" onClick={() => void onRefresh()}>
            Refresh
          </button>
        ) : null}
      </div>

      {expanded ? (
        <div className="memory-debug-panel" aria-label="Memory debug panel">
          {snapshot ? (
            <>
              <div className="memory-debug-metrics" aria-label="Memory debug metrics">
                <span>Nodes {snapshot.nodes.length}</span>
                <span>Edges {snapshot.edges.length}</span>
                <span>Summaries {snapshot.summaries.length}</span>
                <span>Events {snapshot.recentEvents.length}</span>
              </div>
              <div className="memory-debug-grid">
                <DebugColumn title="Nodes">
                  {snapshot.nodes.slice(0, 8).map((node) => (
                    <article className="memory-debug-item" key={node.id}>
                      <span className="memory-debug-kicker">
                        {node.type} / {node.state} / {node.importance.toFixed(2)}
                      </span>
                      <p>{node.content}</p>
                    </article>
                  ))}
                </DebugColumn>
                <DebugColumn title="Edges">
                  {snapshot.edges.slice(0, 8).map((edge) => (
                    <article className="memory-debug-item" key={edge.id}>
                      <span className="memory-debug-kicker">{edge.relationType}</span>
                      <p>
                        {shortId(edge.sourceNodeId)} {"->"} {shortId(edge.targetNodeId)} / {edge.weight.toFixed(2)}
                      </p>
                    </article>
                  ))}
                </DebugColumn>
                <DebugColumn title="Recent Events">
                  {snapshot.recentEvents.slice(0, 8).map((event) => (
                    <article className="memory-debug-item" key={event.id}>
                      <span className="memory-debug-kicker">{formatTime(event.createdAt)}</span>
                      <p>{event.eventType}</p>
                    </article>
                  ))}
                </DebugColumn>
              </div>
            </>
          ) : (
            <p className="memory-debug-empty">No memory snapshot loaded.</p>
          )}
        </div>
      ) : null}
    </section>
  );
}

function DebugColumn({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="memory-debug-column">
      <h2>{title}</h2>
      <div className="memory-debug-list">{children}</div>
    </section>
  );
}

function shortId(value: string): string {
  return value.replace(/^memory_/, "").slice(0, 10);
}

function formatTime(value: string): string {
  return new Date(value).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  });
}
