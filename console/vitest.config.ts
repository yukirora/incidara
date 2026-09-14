import { defineConfig } from "vitest/config";
export default defineConfig({
  test: {
    environment: "node",
    include: ["server/src/**/*.test.ts"],
    // Run test files sequentially to avoid DB state conflicts between integration tests
    pool: "forks",
    poolOptions: {
      forks: {
        singleFork: true,
      },
    },
    // Ensure test files run sequentially (not concurrently)
    sequence: {
      concurrent: false,
    },
    // Also ensure tests within files don't run concurrently
    fileParallelism: false,
  },
});
