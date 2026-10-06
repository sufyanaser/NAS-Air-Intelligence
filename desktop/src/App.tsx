import { useEffect, useState } from "react";
import { fetchSidecarHealth, waitForSidecar, type HealthFetcher, type HealthState } from "./health";

interface AppProps {
  fetcher?: HealthFetcher;
  intervalMs?: number;
}

export default function App({ fetcher = fetchSidecarHealth, intervalMs }: AppProps) {
  const [state, setState] = useState<HealthState>({ phase: "starting", attempts: 0 });

  useEffect(() => {
    const controller = new AbortController();
    void waitForSidecar(fetcher, setState, { intervalMs, signal: controller.signal });
    return () => controller.abort();
  }, [fetcher, intervalMs]);

  return (
    <main className="shell">
      <h1>NAS Air Intelligence</h1>
      <p className="subtitle">Radio monitoring control center</p>
      <section aria-label="Engine status" className={`status status-${state.phase}`}>
        {state.phase === "starting" && <p role="status">Starting analysis engine…</p>}
        {state.phase === "ready" && (
          <>
            <p role="status">Engine ready</p>
            <dl>
              <dt>Core version</dt>
              <dd>{state.health.version}</dd>
              <dt>Python</dt>
              <dd>{state.health.python}</dd>
              <dt>FFmpeg</dt>
              <dd>{state.health.ffmpeg && state.health.ffprobe ? "found" : "not found"}</dd>
            </dl>
          </>
        )}
        {state.phase === "failed" && <p role="alert">Engine unavailable: {state.error}</p>}
      </section>
    </main>
  );
}
