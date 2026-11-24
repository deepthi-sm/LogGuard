import { useState, type FormEvent } from "react";
import { EyebrowLabel } from "../components/EyebrowLabel";

/**
 * /connect: fetch a log file from an HTTP(S) URL. The backend downloads
 * it and streams the lines through the same pipeline as /upload.
 * Plain-text logs and NDJSON `{line, source}` bodies are both accepted.
 */
export function Connect() {
  const [url, setUrl] = useState("");
  const [banner, setBanner] = useState<{
    kind: "info" | "success" | "error";
    message: string;
  } | null>(null);
  const [saving, setSaving] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!url.trim()) {
      setBanner({ kind: "error", message: "Enter a URL first." });
      return;
    }
    setSaving(true);
    setBanner(null);
    try {
      const res = await fetch("/api/v1/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url.trim() }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({ detail: res.statusText }));
        setBanner({
          kind: "error",
          message: `Fetch failed: ${detail.detail ?? res.statusText}`,
        });
        return;
      }
      const data = (await res.json()) as {
        source: string;
        total_lines: number;
        rate: number;
      };
      setBanner({
        kind: "success",
        message:
          `Fetched ${data.total_lines.toLocaleString()} lines (source: ${data.source}). ` +
          `Streaming at ${data.rate}/s; anomalies appear on the dashboard as they are scored.`,
      });
    } catch (err) {
      setBanner({ kind: "error", message: `Fetch failed: ${(err as Error).message}` });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="pb-12">
      <header className="mb-7 border-b-[0.5px] border-border-subtle pb-5">
        <EyebrowLabel>Integrations</EyebrowLabel>
        <h1 className="text-[22px] font-medium leading-none tracking-[-0.01em] text-primary">
          Fetch logs from a URL
        </h1>
        <p className="mt-2 max-w-[640px] text-[13px] text-secondary">
          Point LogGuard at a log file served over HTTP(S). The server
          downloads it (up to 50 MB) and runs every line through the
          detection pipeline, the same way an upload does.
        </p>
      </header>

      <form onSubmit={onSubmit} className="max-w-[560px] space-y-5">
        <Field
          id="log-url"
          label="Log file URL"
          hint="Plain-text logs, one line per event, or NDJSON objects with `line` and `source` fields."
          placeholder="https://example.com/logs/app.log"
          value={url}
          onChange={setUrl}
          type="url"
        />

        {banner && <Banner kind={banner.kind}>{banner.message}</Banner>}

        <div className="flex items-center gap-3 pt-2">
          <button
            type="submit"
            disabled={saving}
            className="rounded-lg bg-iris px-4 py-2.5 text-[13px] font-medium text-page transition-colors hover:bg-iris-deep disabled:cursor-not-allowed disabled:opacity-60"
          >
            {saving ? "Fetching…" : "Fetch and analyse"}
          </button>
        </div>
      </form>
    </div>
  );
}

function Field({
  id,
  label,
  hint,
  placeholder,
  value,
  onChange,
  type = "text",
}: {
  id: string;
  label: string;
  hint: string;
  placeholder: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
}) {
  return (
    <div>
      <label
        htmlFor={id}
        className="mb-2 block text-[11px] uppercase tracking-[0.08em] text-tertiary"
      >
        {label}
      </label>
      <input
        id={id}
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-md border-[0.5px] border-border-subtle bg-card px-3.5 py-2.5 text-[13px] text-primary placeholder:text-muted focus:border-iris focus:outline-none"
      />
      <p className="mt-1.5 text-[11px] text-tertiary">{hint}</p>
    </div>
  );
}

function Banner({
  kind,
  children,
}: {
  kind: "info" | "success" | "error";
  children: React.ReactNode;
}) {
  const styles =
    kind === "info"
      ? "border-iris/40 bg-iris/10 text-iris"
      : kind === "success"
        ? "border-success/40 bg-success/10 text-success"
        : "border-critical/40 bg-critical/10 text-critical";
  return (
    <div
      className={`rounded-md border-[0.5px] px-3 py-2 text-[12px] ${styles}`}
      role="status"
    >
      {children}
    </div>
  );
}
