import type { ReactNode } from "react";
import type { MemoryDebugSnapshot } from "../../../../shared/contracts/ipc";
import type { PetSettings } from "../../../../shared/schemas/petSettings";
import { t } from "../../i18n";

export interface MemoryDebugPanelProps {
  snapshot?: MemoryDebugSnapshot;
  expanded: boolean;
  displayLanguage?: PetSettings["displayLanguage"];
  onToggle(): void;
  onRefresh(): Promise<void>;
}

export function MemoryDebugPanel({ snapshot, expanded, displayLanguage = "en", onToggle, onRefresh }: MemoryDebugPanelProps) {
  return (
    <section className="memory-debug-shell" aria-label={t(displayLanguage, "memoryDebugTools")}>
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
          {t(displayLanguage, "memoryDebug")}
        </button>
        {expanded ? (
          <button type="button" className="memory-debug-refresh" onClick={() => void onRefresh()}>
            {t(displayLanguage, "refresh")}
          </button>
        ) : null}
      </div>

      {expanded ? (
        <div className="memory-debug-panel" aria-label={t(displayLanguage, "memoryDebugPanel")}>
          <MemoryDebugContent snapshot={snapshot} displayLanguage={displayLanguage} />
        </div>
      ) : null}
    </section>
  );
}

export function MemoryDebugContent({
  snapshot,
  displayLanguage = "en"
}: {
  snapshot?: MemoryDebugSnapshot;
  displayLanguage?: PetSettings["displayLanguage"];
}) {
  return snapshot ? (
    <>
      <div className="memory-debug-metrics" aria-label={t(displayLanguage, "memoryDebugMetrics")}>
        <span>{t(displayLanguage, "nodes")} {snapshot.nodes.length}</span>
        <span>{t(displayLanguage, "edges")} {snapshot.edges.length}</span>
        <span>{t(displayLanguage, "summaries")} {snapshot.summaries.length}</span>
        <span>{t(displayLanguage, "events")} {snapshot.recentEvents.length}</span>
      </div>
      {snapshot.lastRecallTrace ? <LastRecallTrace snapshot={snapshot} displayLanguage={displayLanguage} /> : null}
      <div className="memory-debug-grid">
        <DebugColumn title={t(displayLanguage, "nodes")}>
          {snapshot.nodes.slice(0, 8).map((node) => (
            <article className="memory-debug-item" key={node.id}>
              <span className="memory-debug-kicker">
                {node.type} / {node.state} / {node.importance.toFixed(2)}
              </span>
              <p>{node.content}</p>
            </article>
          ))}
        </DebugColumn>
        <DebugColumn title={t(displayLanguage, "edges")}>
          {snapshot.edges.slice(0, 8).map((edge) => (
            <article className="memory-debug-item" key={edge.id}>
              <span className="memory-debug-kicker">{edge.relationType}</span>
              <p>
                {shortId(edge.sourceNodeId)} {"->"} {shortId(edge.targetNodeId)} / {edge.weight.toFixed(2)}
              </p>
            </article>
          ))}
        </DebugColumn>
        <DebugColumn title={t(displayLanguage, "recentEvents")}>
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
    <p className="memory-debug-empty">{t(displayLanguage, "noMemorySnapshot")}</p>
  );
}

function LastRecallTrace({
  snapshot,
  displayLanguage
}: {
  snapshot: MemoryDebugSnapshot;
  displayLanguage: PetSettings["displayLanguage"];
}) {
  const trace = snapshot.lastRecallTrace;

  if (!trace) {
    return null;
  }

  return (
    <section className="memory-debug-recall" aria-label={t(displayLanguage, "lastMemoryRecallTrace")}>
      <div className="memory-debug-recall-header">
        <h2>{t(displayLanguage, "lastRecall")}</h2>
        <span>{t(displayLanguage, "seeds")} {trace.seedNodeIds.length}</span>
        <span>{t(displayLanguage, "expanded")} {trace.expandedNodeIds.length}</span>
        <span>{t(displayLanguage, "returned")} {trace.returnedNodeIds.length}</span>
      </div>
      <p className="memory-debug-query">{trace.query}</p>
      <div className="memory-debug-chip-row">
        {trace.recallPlan.queryTerms.map((term) => (
          <span key={term}>{term}</span>
        ))}
        {trace.recallPlan.targetTypes.map((type) => (
          <span key={type}>{type}</span>
        ))}
      </div>
      <div className="memory-debug-path-list">
        {trace.traversedEdges.slice(0, 6).map((edge) => (
          <p key={`${edge.sourceNodeId}-${edge.targetNodeId}-${edge.relationType ?? "relates_to"}-${edge.depth}`}>
            {shortId(edge.sourceNodeId)} {"->"} {shortId(edge.targetNodeId)} /{" "}
            {edge.relationType ?? "relates_to"} / d{edge.depth}
            {typeof edge.weight === "number" ? ` / ${edge.weight.toFixed(2)}` : ""}
          </p>
        ))}
      </div>
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
