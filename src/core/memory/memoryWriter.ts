import PQueue from "p-queue";

export class MemoryWriteQueue {
  private readonly queue = new PQueue({ concurrency: 1 });

  enqueue<T>(writeOperation: () => Promise<T> | T): Promise<T> {
    return this.queue.add(async () => writeOperation()) as Promise<T>;
  }

  get pending(): number {
    return this.queue.pending;
  }

  get size(): number {
    return this.queue.size;
  }
}
