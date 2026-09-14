import { describe, it, expect, vi, beforeEach } from "vitest";
import { collectGpuSnapshot } from "./gpu-collector.js";

// Mock fetch for Prometheus queries
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

// Mock pg pool
function makeMockPool() {
  const queries: { sql: string; params: any[] }[] = [];
  const client = {
    query: vi.fn(async (sql: string, params?: any[]) => {
      queries.push({ sql, params: params ?? [] });
      return { rows: [], rowCount: 0 };
    }),
    release: vi.fn(),
  };
  const pool = {
    connect: vi.fn(async () => client),
    query: vi.fn(),
    _queries: queries,
    _client: client,
  };
  return pool as any;
}

function promResponse(results: { metric: Record<string, string>; value: [number, string] }[]) {
  return {
    ok: true,
    json: async () => ({ data: { result: results } }),
  };
}

describe("gpu-collector", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("collects snapshot from Prometheus and inserts into DB", async () => {
    const pool = makeMockPool();

    // Mock 7 Prometheus queries in order:
    // 1. resourcesTotal by (vc_stat, sku)
    // 2. resourcesUsed by (vc_stat, sku)
    // 3. count(task_gpu_percent > 0) by vc
    // 4. count(task_gpu_percent == 0) by vc
    // 5. sum(task_gpu_percent) by vc
    // 6. avg(task_gpu_percent > 0) by vc
    // 7. unhealthy nodes

    mockFetch
      .mockResolvedValueOnce(promResponse([
        { metric: { vc_stat: "h200pretraining", sku: "h200" }, value: [0, "4248"] },
        { metric: { vc_stat: "h200agentic", sku: "h200" }, value: [0, "1072"] },
      ]))
      .mockResolvedValueOnce(promResponse([
        { metric: { vc_stat: "h200pretraining", sku: "h200" }, value: [0, "4200"] },
        { metric: { vc_stat: "h200agentic", sku: "h200" }, value: [0, "1020"] },
      ]))
      .mockResolvedValueOnce(promResponse([
        { metric: { virtual_cluster: "h200pretraining" }, value: [0, "3800"] },
        { metric: { virtual_cluster: "h200agentic" }, value: [0, "625"] },
      ]))
      .mockResolvedValueOnce(promResponse([
        { metric: { virtual_cluster: "h200pretraining" }, value: [0, "400"] },
        { metric: { virtual_cluster: "h200agentic" }, value: [0, "395"] },
      ]))
      .mockResolvedValueOnce(promResponse([
        { metric: { virtual_cluster: "h200pretraining" }, value: [0, "350000"] }, // sum/100 = 3500
        { metric: { virtual_cluster: "h200agentic" }, value: [0, "58750"] },     // sum/100 = 587.5
      ]))
      .mockResolvedValueOnce(promResponse([
        { metric: { virtual_cluster: "h200pretraining" }, value: [0, "92.1"] },
        { metric: { virtual_cluster: "h200agentic" }, value: [0, "94.0"] },
      ]))
      .mockResolvedValueOnce(promResponse([])); // no unhealthy

    const count = await collectGpuSnapshot(pool);

    expect(count).toBe(2); // 2 VCs
    expect(pool.connect).toHaveBeenCalledOnce();
    expect(pool._client.query).toHaveBeenCalled();

    // Verify BEGIN, 2 INSERTs, COMMIT (no DELETE — cleanup is in startup only)
    const calls = pool._client.query.mock.calls;
    expect(calls[0][0]).toBe("BEGIN");
    expect(calls[1][0]).toContain("INSERT INTO gpu_utilization_snapshots");
    expect(calls[2][0]).toContain("INSERT INTO gpu_utilization_snapshots");
    expect(calls[3][0]).toBe("COMMIT");

    // Verify first INSERT params (h200pretraining)
    // Params: [timestamp, sku, vc, total, used, active, idle, sum_util, avg_util, unhealthy]
    const insert1Params = calls[1][1];
    expect(insert1Params[1]).toBe("h200"); // sku
    expect(insert1Params[2]).toBe("h200pretraining"); // vc
    expect(insert1Params[3]).toBe(4248); // total_gpus
    expect(insert1Params[4]).toBe(4200); // used_gpus
    expect(insert1Params[5]).toBe(3800); // active_gpus
    expect(insert1Params[6]).toBe(400); // idle_gpus
    // sum_gpu_util = 350000 / 100 = 3500
    expect(insert1Params[7]).toBe(3500);
    expect(insert1Params[8]).toBe(92.1); // avg_gpu_util
    expect(insert1Params[9]).toBe(0); // unhealthy

    // Verify second INSERT params (h200agentic)
    const insert2Params = calls[2][1];
    expect(insert2Params[1]).toBe("h200");
    expect(insert2Params[2]).toBe("h200agentic");
    expect(insert2Params[3]).toBe(1072);
    expect(insert2Params[4]).toBe(1020);
    expect(insert2Params[5]).toBe(625);
    expect(insert2Params[6]).toBe(395);
    expect(insert2Params[7]).toBe(587.5);
    expect(insert2Params[8]).toBe(94.0);
  });

  it("handles empty Prometheus response gracefully", async () => {
    const pool = makeMockPool();

    // All queries return empty
    for (let i = 0; i < 7; i++) {
      mockFetch.mockResolvedValueOnce(promResponse([]));
    }

    const count = await collectGpuSnapshot(pool);
    expect(count).toBe(0);
    // No DB interaction expected
    expect(pool.connect).not.toHaveBeenCalled();
  });

  it("handles Prometheus fetch error gracefully", async () => {
    const pool = makeMockPool();

    mockFetch.mockRejectedValueOnce(new Error("connection refused"));

    await expect(collectGpuSnapshot(pool)).rejects.toThrow("connection refused");
  });

  it("rolls back on DB error", async () => {
    const pool = makeMockPool();

    // Prometheus returns data
    mockFetch
      .mockResolvedValueOnce(promResponse([{ metric: { vc_stat: "test", sku: "h200" }, value: [0, "100"] }]))
      .mockResolvedValueOnce(promResponse([{ metric: { vc_stat: "test", sku: "h200" }, value: [0, "80"] }]))
      .mockResolvedValueOnce(promResponse([{ metric: { virtual_cluster: "test" }, value: [0, "60"] }]))
      .mockResolvedValueOnce(promResponse([{ metric: { virtual_cluster: "test" }, value: [0, "20"] }]))
      .mockResolvedValueOnce(promResponse([{ metric: { virtual_cluster: "test" }, value: [0, "5500"] }]))
      .mockResolvedValueOnce(promResponse([{ metric: { virtual_cluster: "test" }, value: [0, "91.7"] }]))
      .mockResolvedValueOnce(promResponse([]));

    // Make the INSERT fail
    pool._client.query.mockImplementation(async (sql: string) => {
      if (sql === "BEGIN") return;
      if (sql.includes("INSERT")) throw new Error("unique constraint violated");
      return { rows: [], rowCount: 0 };
    });

    await expect(collectGpuSnapshot(pool)).rejects.toThrow("unique constraint violated");

    // Verify ROLLBACK was called
    const calls = pool._client.query.mock.calls;
    const lastCall = calls[calls.length - 1][0];
    expect(lastCall).toBe("ROLLBACK");
  });
});
