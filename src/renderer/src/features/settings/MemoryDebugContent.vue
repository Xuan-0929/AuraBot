<template>
  <template v-if="snapshot">
    <div class="memory-debug-metrics" :aria-label="t(displayLanguage, 'memoryDebugMetrics')">
      <span>{{ t(displayLanguage, "nodes") }} {{ snapshot.nodes.length }}</span>
      <span>{{ t(displayLanguage, "edges") }} {{ snapshot.edges.length }}</span>
      <span>{{ t(displayLanguage, "summaries") }} {{ snapshot.summaries.length }}</span>
      <span>{{ t(displayLanguage, "events") }} {{ snapshot.recentEvents.length }}</span>
    </div>
    <section
      v-if="snapshot.lastRecallTrace"
      class="memory-debug-recall"
      :aria-label="t(displayLanguage, 'lastMemoryRecallTrace')"
    >
      <div class="memory-debug-recall-header">
        <h2>{{ t(displayLanguage, "lastRecall") }}</h2>
        <span>{{ t(displayLanguage, "seeds") }} {{ snapshot.lastRecallTrace.seedNodeIds.length }}</span>
        <span>{{ t(displayLanguage, "expanded") }} {{ snapshot.lastRecallTrace.expandedNodeIds.length }}</span>
        <span>{{ t(displayLanguage, "returned") }} {{ snapshot.lastRecallTrace.returnedNodeIds.length }}</span>
      </div>
      <p class="memory-debug-query">{{ snapshot.lastRecallTrace.query }}</p>
      <div class="memory-debug-chip-row">
        <span v-for="term in snapshot.lastRecallTrace.recallPlan.queryTerms" :key="term">{{ term }}</span>
        <span v-for="type in snapshot.lastRecallTrace.recallPlan.targetTypes" :key="type">{{ type }}</span>
      </div>
      <div class="memory-debug-path-list">
        <p v-for="edge in snapshot.lastRecallTrace.traversedEdges.slice(0, 6)" :key="`${edge.sourceNodeId}-${edge.targetNodeId}-${edge.relationType ?? 'relates_to'}-${edge.depth}`">
          {{ shortId(edge.sourceNodeId) }} -&gt; {{ shortId(edge.targetNodeId) }} /
          {{ edge.relationType ?? "relates_to" }} / d{{ edge.depth
          }}{{ typeof edge.weight === "number" ? ` / ${edge.weight.toFixed(2)}` : "" }}
        </p>
      </div>
    </section>
    <div class="memory-debug-grid">
      <section class="memory-debug-column">
        <h2>{{ t(displayLanguage, "nodes") }}</h2>
        <div class="memory-debug-list">
          <article v-for="node in snapshot.nodes.slice(0, 8)" :key="node.id" class="memory-debug-item">
            <span class="memory-debug-kicker">{{ node.type }} / {{ node.state }} / {{ node.importance.toFixed(2) }}</span>
            <p>{{ node.content }}</p>
          </article>
        </div>
      </section>
      <section class="memory-debug-column">
        <h2>{{ t(displayLanguage, "edges") }}</h2>
        <div class="memory-debug-list">
          <article v-for="edge in snapshot.edges.slice(0, 8)" :key="edge.id" class="memory-debug-item">
            <span class="memory-debug-kicker">{{ edge.relationType }}</span>
            <p>{{ shortId(edge.sourceNodeId) }} -&gt; {{ shortId(edge.targetNodeId) }} / {{ edge.weight.toFixed(2) }}</p>
          </article>
        </div>
      </section>
      <section class="memory-debug-column">
        <h2>{{ t(displayLanguage, "recentEvents") }}</h2>
        <div class="memory-debug-list">
          <article v-for="event in snapshot.recentEvents.slice(0, 8)" :key="event.id" class="memory-debug-item">
            <span class="memory-debug-kicker">{{ formatTime(event.createdAt) }}</span>
            <p>{{ event.eventType }}</p>
          </article>
        </div>
      </section>
    </div>
  </template>
  <p v-else class="memory-debug-empty">{{ t(displayLanguage, "noMemorySnapshot") }}</p>
</template>

<script setup lang="ts">
import type { MemoryDebugSnapshot } from "../../../../shared/contracts/ipc";
import type { PetSettings } from "../../../../shared/schemas/petSettings";
import { t } from "../../i18n";

withDefaults(
  defineProps<{
    snapshot?: MemoryDebugSnapshot;
    displayLanguage?: PetSettings["displayLanguage"];
  }>(),
  { displayLanguage: "en" }
);

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
</script>
