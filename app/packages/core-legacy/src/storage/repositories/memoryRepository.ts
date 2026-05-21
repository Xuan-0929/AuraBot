import { randomUUID } from "node:crypto";
import type {
  MemoryEdge,
  MemoryNode,
  MemorySummary,
  MemoryWriteRequest
} from "../../../../shared/src/schemas/memory";
import type { AppDatabase } from "../db";

interface MemoryNodeRow {
  id: string;
  profile_id: string;
  type: MemoryNode["type"];
  state: MemoryNode["state"];
  content: string;
  importance: number;
  starts_at: string | null;
  ends_at: string | null;
  created_at: string;
  updated_at: string;
}

interface MemoryEdgeRow {
  id: string;
  profile_id: string;
  source_node_id: string;
  target_node_id: string;
  relation_type: MemoryEdge["relationType"];
  weight: number;
  created_at: string;
}

interface MemorySummaryRow {
  id: string;
  profile_id: string;
  topic: string;
  content: string;
  updated_at: string;
}

export class MemoryRepository {
  constructor(private readonly database: AppDatabase) {}

  createNodeFromWriteRequest(request: MemoryWriteRequest): string {
    const now = new Date().toISOString();
    let nodeId = `memory_${randomUUID()}`;

    const write = this.database.transaction(() => {
      const existing = this.database
        .prepare(
          `
          select id from memory_nodes
          where profile_id = ?
            and type = ?
            and content = ?
            and state != 'archived'
          limit 1
        `
        )
        .get(request.profileId, request.node.type, request.node.content) as { id: string } | undefined;

      if (existing) {
        nodeId = existing.id;
        this.database
          .prepare(
            `
            update memory_nodes
            set importance = max(importance, ?),
                updated_at = ?
            where id = ?
          `
          )
          .run(request.node.importance, now, nodeId);
      } else {
        this.database
          .prepare(
            `
            insert into memory_nodes (
              id, profile_id, type, state, content, importance, starts_at, ends_at, created_at, updated_at
            )
            values (@id, @profileId, @type, 'active', @content, @importance, @startsAt, @endsAt, @createdAt, @updatedAt)
          `
          )
          .run({
            id: nodeId,
            profileId: request.profileId,
            type: request.node.type,
            content: request.node.content,
            importance: request.node.importance,
            startsAt: request.node.startsAt ?? null,
            endsAt: request.node.endsAt ?? null,
            createdAt: now,
            updatedAt: now
          });

        this.upsertSummary({
          profileId: request.profileId,
          topic: request.node.type,
          content: request.node.content,
          updatedAt: now
        });
      }

      for (const edge of request.edges) {
        this.database
          .prepare(
            `
            insert or ignore into memory_edges (
              id, profile_id, source_node_id, target_node_id, relation_type, weight, created_at
            )
            values (@id, @profileId, @sourceNodeId, @targetNodeId, @relationType, @weight, @createdAt)
          `
          )
          .run({
            id: `memory_edge_${randomUUID()}`,
            profileId: request.profileId,
            sourceNodeId: nodeId,
            targetNodeId: edge.targetNodeId,
            relationType: edge.relationType,
            weight: edge.weight,
            createdAt: now
          });
      }
    });

    write();
    return nodeId;
  }

  updateNodeFromWriteRequest(input: {
    profileId: string;
    nodeId: string;
    node: MemoryWriteRequest["node"];
  }): string {
    const now = new Date().toISOString();

    const write = this.database.transaction(() => {
      this.database
        .prepare(
          `
          update memory_nodes
          set content = @content,
              importance = max(importance, @importance),
              starts_at = coalesce(@startsAt, starts_at),
              ends_at = coalesce(@endsAt, ends_at),
              updated_at = @updatedAt
          where id = @nodeId
            and profile_id = @profileId
            and state != 'archived'
        `
        )
        .run({
          nodeId: input.nodeId,
          profileId: input.profileId,
          content: input.node.content,
          importance: input.node.importance,
          startsAt: input.node.startsAt ?? null,
          endsAt: input.node.endsAt ?? null,
          updatedAt: now
        });

      this.upsertSummary({
        profileId: input.profileId,
        topic: input.node.type,
        content: input.node.content,
        updatedAt: now
      });
    });

    write();
    return input.nodeId;
  }

  updateNodeState(input: {
    profileId: string;
    nodeId: string;
    state: MemoryNode["state"];
  }): void {
    this.database
      .prepare(
        `
        update memory_nodes
        set state = @state,
            updated_at = @updatedAt
        where id = @nodeId
          and profile_id = @profileId
          and state != 'archived'
      `
      )
      .run({
        profileId: input.profileId,
        nodeId: input.nodeId,
        state: input.state,
        updatedAt: new Date().toISOString()
      });
  }

  getNodesByIds(ids: string[]): MemoryNode[] {
    if (ids.length === 0) {
      return [];
    }

    const placeholders = ids.map(() => "?").join(", ");
    return (
      this.database
        .prepare(`select * from memory_nodes where id in (${placeholders})`)
        .all(...ids) as MemoryNodeRow[]
    ).map(mapNode);
  }

  getMemoryContext(profileId: string): {
    nodes: MemoryNode[];
    edges: MemoryEdge[];
    summaries: MemorySummary[];
  } {
    return {
      nodes: (
        this.database
          .prepare(
            `
            select * from memory_nodes
            where profile_id = ? and state != 'archived'
            order by importance desc, updated_at desc
          `
          )
          .all(profileId) as MemoryNodeRow[]
      ).map(mapNode),
      edges: (
        this.database
          .prepare("select * from memory_edges where profile_id = ?")
          .all(profileId) as MemoryEdgeRow[]
      ).map(mapEdge),
      summaries: (
        this.database.prepare("select * from memory_summaries where profile_id = ?").all(profileId) as MemorySummaryRow[]
      ).map(mapSummary)
    };
  }

  private upsertSummary(input: {
    profileId: string;
    topic: string;
    content: string;
    updatedAt: string;
  }): void {
    this.database
      .prepare(
        `
        insert into memory_summaries (id, profile_id, topic, content, updated_at)
        values (@id, @profileId, @topic, @content, @updatedAt)
        on conflict(profile_id, topic) do update set
          content = memory_summaries.content || char(10) || excluded.content,
          updated_at = excluded.updated_at
      `
      )
      .run({
        id: `memory_summary_${randomUUID()}`,
        profileId: input.profileId,
        topic: input.topic,
        content: input.content,
        updatedAt: input.updatedAt
      });
  }
}

function mapNode(row: MemoryNodeRow): MemoryNode {
  return {
    id: row.id,
    profileId: row.profile_id,
    type: row.type,
    state: row.state,
    content: row.content,
    importance: row.importance,
    startsAt: row.starts_at ?? undefined,
    endsAt: row.ends_at ?? undefined,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

function mapEdge(row: MemoryEdgeRow): MemoryEdge {
  return {
    id: row.id,
    profileId: row.profile_id,
    sourceNodeId: row.source_node_id,
    targetNodeId: row.target_node_id,
    relationType: row.relation_type,
    weight: row.weight,
    createdAt: row.created_at
  };
}

function mapSummary(row: MemorySummaryRow): MemorySummary {
  return {
    id: row.id,
    profileId: row.profile_id,
    topic: row.topic,
    content: row.content,
    updatedAt: row.updated_at
  };
}
