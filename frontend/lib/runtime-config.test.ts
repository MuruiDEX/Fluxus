import { describe, expect, it } from "vitest";
import { resolveApiOrigin, resolveAppMode, RuntimeConfigError } from "@/lib/runtime-config";

const PROD = "production";

describe("runtime mode", () => {
  it("defaults to demo in development", () => {
    expect(resolveAppMode({ nodeEnv: "development" })).toBe("demo");
  });

  it("requires an explicit mode for production", () => {
    expect(() => resolveAppMode({ nodeEnv: PROD })).toThrow(RuntimeConfigError);
  });

  it("rejects unknown modes", () => {
    expect(() => resolveAppMode({ mode: "staging", nodeEnv: PROD })).toThrow(RuntimeConfigError);
  });

  it("accepts demo and live case-insensitively", () => {
    expect(resolveAppMode({ mode: " DEMO ", nodeEnv: PROD })).toBe("demo");
    expect(resolveAppMode({ mode: "Live", nodeEnv: PROD })).toBe("live");
  });
});

describe("API origin", () => {
  it("requires an origin in live mode", () => {
    expect(() => resolveApiOrigin({ mode: "live", nodeEnv: PROD })).toThrow(RuntimeConfigError);
  });

  it("returns the development-origin placeholder for untargeted demo", () => {
    expect(resolveApiOrigin({ mode: "demo", nodeEnv: "development" })).toBe(
      "http://127.0.0.1:8000",
    );
  });

  it("normalizes a valid production https origin", () => {
    expect(
      resolveApiOrigin({
        mode: "live",
        apiUrl: "https://app.example.com/",
        nodeEnv: PROD,
      }),
    ).toBe("https://app.example.com");
  });

  it("rejects paths, queries, fragments, credentials, and relative URLs", () => {
    for (const apiUrl of [
      "https://app.example.com/api",
      "https://app.example.com?x=1",
      "https://app.example.com#x",
      "https://user:pass@app.example.com",
      "app.example.com",
      "ftp://app.example.com",
    ]) {
      expect(() => resolveApiOrigin({ mode: "live", apiUrl, nodeEnv: PROD })).toThrow(
        RuntimeConfigError,
      );
    }
  });

  it("rejects loopback and http in production live builds", () => {
    for (const apiUrl of [
      "http://api.example.com",
      "https://127.0.0.1:8000",
      "https://localhost",
    ]) {
      expect(() => resolveApiOrigin({ mode: "live", apiUrl, nodeEnv: PROD })).toThrow(
        RuntimeConfigError,
      );
    }
  });

  it("allows explicit test-only loopback http", () => {
    expect(
      resolveApiOrigin({
        mode: "live",
        apiUrl: "http://127.0.0.1:8000",
        nodeEnv: PROD,
        allowInsecureApi: "1",
      }),
    ).toBe("http://127.0.0.1:8000");
  });
});
