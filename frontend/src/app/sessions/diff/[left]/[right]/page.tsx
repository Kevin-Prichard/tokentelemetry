"use client";

import React, { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { API_BASE } from "@/lib/api";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";

interface DiffBlock {
  tag: "equal" | "replace" | "insert" | "delete";
  left: string[];
  right: string[];
}

export default function SessionDiffPage() {
  const { left, right } = useParams<{ left: string; right: string }>();
  const [diffs, setDiffs] = useState<DiffBlock[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!left || !right) return;
    fetch(`${API_BASE}/sessions/diff/${left}/${right}`)
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.json();
      })
      .then((d) => {
        setDiffs(d);
        setLoading(false);
      })
      .catch((e) => {
        setError(String(e));
        setLoading(false);
      });
  }, [left, right]);

  return (
    <div className="px-8 py-8 max-w-[1800px] mx-auto pb-20">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <Link
          href="/"
          className="text-[var(--tt-fg-muted)] hover:text-[var(--tt-brand)] transition-colors"
        >
          <ArrowLeft size={16} />
        </Link>
        <div>
          <div className="text-[10px] uppercase tracking-[0.18em] text-[var(--tt-fg-dim)] mb-0.5">
            Session Diff
          </div>
          <div className="font-mono text-[12px] text-[var(--tt-fg-muted)]">
            <span className="text-sky-400">{left?.slice(0, 20)}…</span>
            <span className="mx-2 text-[var(--tt-fg-faint)]">vs</span>
            <span className="text-emerald-400">{right?.slice(0, 20)}…</span>
          </div>
        </div>
      </div>

      {loading && (
        <div className="text-[var(--tt-fg-dim)] text-sm py-16 text-center animate-pulse">
          Computing diff…
        </div>
      )}

      {error && (
        <div className="text-rose-400 text-sm py-8 text-center">
          Error: {error}
        </div>
      )}

      {diffs && (
        <>
          {/* Column headers */}
          <div className="grid grid-cols-2 gap-0 border border-[var(--tt-border)] rounded-t-lg overflow-hidden">
            <div className="px-4 py-2 text-[10px] uppercase tracking-[0.18em] text-sky-400 font-semibold bg-sky-500/5 border-r border-[var(--tt-border)]">
              ← Left (earlier) — <span className="font-mono normal-case">{left?.slice(0, 24)}…</span>
            </div>
            <div className="px-4 py-2 text-[10px] uppercase tracking-[0.18em] text-emerald-400 font-semibold bg-emerald-500/5">
              Right (later) → — <span className="font-mono normal-case">{right?.slice(0, 24)}…</span>
            </div>
          </div>

          {/* Diff body */}
          <div className="border border-t-0 border-[var(--tt-border)] rounded-b-lg overflow-hidden font-mono text-[12px] leading-[1.65]">
            {diffs.length === 0 && (
              <div className="text-center py-10 text-[var(--tt-fg-dim)] text-sm font-sans">
                Sessions are identical — no differences found.
              </div>
            )}
            {diffs.map((block, bi) => (
              <DiffBlockRow key={bi} block={block} />
            ))}
          </div>

          {/* Legend */}
          <div className="mt-4 flex items-center gap-5 text-[11px] text-[var(--tt-fg-dim)]">
            <span className="flex items-center gap-1.5">
              <span className="w-3 h-3 rounded-sm bg-rose-500/20 border border-rose-500/30" />
              Removed
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-3 h-3 rounded-sm bg-emerald-500/20 border border-emerald-500/30" />
              Added
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-3 h-3 rounded-sm bg-amber-500/15 border border-amber-500/30" />
              Changed
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-3 h-3 rounded-sm bg-[var(--tt-tint-1)] border border-[var(--tt-border)]" />
              Unchanged
            </span>
          </div>
        </>
      )}
    </div>
  );
}

function DiffBlockRow({ block }: { block: DiffBlock }) {
  const { tag, left, right } = block;

  const leftBg =
    tag === "delete" ? "bg-rose-500/10" :
    tag === "replace" ? "bg-amber-500/8" :
    "";

  const rightBg =
    tag === "insert" ? "bg-emerald-500/10" :
    tag === "replace" ? "bg-emerald-500/8" :
    "";

  const leftMarker =
    tag === "delete" ? "text-rose-400" :
    tag === "replace" ? "text-amber-400" :
    "text-[var(--tt-fg-faint)]";

  const rightMarker =
    tag === "insert" ? "text-emerald-400" :
    tag === "replace" ? "text-emerald-400" :
    "text-[var(--tt-fg-faint)]";

  const leftPrefix  = tag === "delete" ? "−" : tag === "replace" ? "~" : " ";
  const rightPrefix = tag === "insert" ? "+" : tag === "replace" ? "~" : " ";

  const maxLines = Math.max(left.length, right.length, 1);
  const rows = Array.from({ length: maxLines }, (_, i) => ({
    l: left[i] ?? "",
    r: right[i] ?? "",
  }));

  return (
    <>
      {rows.map((row, i) => (
        <div
          key={i}
          className="grid grid-cols-2 border-b border-[var(--tt-border)] last:border-b-0"
        >
          {/* Left cell */}
          <div className={`flex gap-2 px-3 py-[3px] border-r border-[var(--tt-border)] min-w-0 ${leftBg}`}>
            <span className={`select-none shrink-0 w-3 ${leftMarker}`}>{leftPrefix}</span>
            <span className="whitespace-pre-wrap break-all text-[var(--tt-fg)] min-w-0">
              {row.l}
            </span>
          </div>
          {/* Right cell */}
          <div className={`flex gap-2 px-3 py-[3px] min-w-0 ${rightBg}`}>
            <span className={`select-none shrink-0 w-3 ${rightMarker}`}>{rightPrefix}</span>
            <span className="whitespace-pre-wrap break-all text-[var(--tt-fg)] min-w-0">
              {row.r}
            </span>
          </div>
        </div>
      ))}
    </>
  );
}
