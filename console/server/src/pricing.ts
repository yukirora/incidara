import type { Pool } from "pg";

export interface ModelPricing {
  model: string;
  input_per_m: number;
  output_per_m: number;
  cache_read_per_m: number;
  cache_creation_per_m: number;
}

// Fallback pricing for unknown models (sonnet-level, official Anthropic rates)
const DEFAULT_PRICING: ModelPricing = {
  model: "default",
  input_per_m: 3.0,
  output_per_m: 15.0,
  cache_read_per_m: 0.30,
  cache_creation_per_m: 3.75,
};

export class PricingStore {
  private cache: Map<string, ModelPricing> = new Map();
  private lastRefresh: number = 0;
  private readonly REFRESH_INTERVAL_MS = 5 * 60 * 1000; // 5 min

  constructor(private pool: Pool) {}

  private async ensureLoaded(): Promise<void> {
    const now = Date.now();
    if (now - this.lastRefresh < this.REFRESH_INTERVAL_MS && this.cache.size > 0) return;

    const { rows } = await this.pool.query(
      "SELECT model, input_per_m, output_per_m, cache_read_per_m, cache_creation_per_m FROM model_pricing"
    );
    this.cache.clear();
    for (const row of rows) {
      this.cache.set(row.model, {
        model: row.model,
        input_per_m: Number(row.input_per_m),
        output_per_m: Number(row.output_per_m),
        cache_read_per_m: Number(row.cache_read_per_m),
        cache_creation_per_m: Number(row.cache_creation_per_m),
      });
    }
    this.lastRefresh = now;
  }

  /**
   * Get pricing for a model. Falls back to prefix matching, then default.
   */
  async getPricing(model: string): Promise<ModelPricing> {
    await this.ensureLoaded();

    // Exact match
    if (this.cache.has(model)) return this.cache.get(model)!;

    // Prefix match: e.g. "claude-opus-4-6" matches "claude-opus-4-6-thinking"
    for (const [key, pricing] of this.cache) {
      if (model.startsWith(key)) return pricing;
    }

    // Reverse: key is prefix of model
    for (const [key, pricing] of this.cache) {
      if (key.startsWith(model)) return pricing;
    }

    return DEFAULT_PRICING;
  }

  /**
   * Calculate cost for a set of token counts given a model name.
   */
  async calculateCost(model: string, tokens: {
    input_tokens: number;
    output_tokens: number;
    cache_creation_input_tokens: number;
    cache_read_input_tokens: number;
  }): Promise<number> {
    const pricing = await this.getPricing(model);
    const inputCost = (tokens.input_tokens / 1_000_000) * pricing.input_per_m;
    const outputCost = (tokens.output_tokens / 1_000_000) * pricing.output_per_m;
    const cacheCreateCost = (tokens.cache_creation_input_tokens / 1_000_000) * pricing.cache_creation_per_m;
    const cacheReadCost = (tokens.cache_read_input_tokens / 1_000_000) * pricing.cache_read_per_m;
    return inputCost + outputCost + cacheCreateCost + cacheReadCost;
  }

  /**
   * List all known models from DB.
   */
  async listModels(): Promise<ModelPricing[]> {
    await this.ensureLoaded();
    return Array.from(this.cache.values());
  }

  /**
   * Upsert a model pricing entry.
   */
  async upsertPricing(pricing: ModelPricing): Promise<void> {
    await this.pool.query(
      `INSERT INTO model_pricing (model, input_per_m, output_per_m, cache_read_per_m, cache_creation_per_m)
       VALUES ($1, $2, $3, $4, $5)
       ON CONFLICT (model) DO UPDATE SET
         input_per_m = EXCLUDED.input_per_m,
         output_per_m = EXCLUDED.output_per_m,
         cache_read_per_m = EXCLUDED.cache_read_per_m,
         cache_creation_per_m = EXCLUDED.cache_creation_per_m,
         updated_at = NOW()`,
      [pricing.model, pricing.input_per_m, pricing.output_per_m, pricing.cache_read_per_m, pricing.cache_creation_per_m]
    );
    this.lastRefresh = 0; // force refresh on next read
  }

  /**
   * Force refresh the cache.
   */
  invalidateCache(): void {
    this.lastRefresh = 0;
  }
}
