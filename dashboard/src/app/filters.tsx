"use client";

import { useRouter, useSearchParams, usePathname } from "next/navigation";
import { useCallback } from "react";

const SCORE_OPTIONS = [
  { label: "All", value: "" },
  { label: "HIGH", value: "high" },
  { label: "MED", value: "medium" },
  { label: "LOW", value: "low" },
];

const SIZE_OPTIONS = [
  { label: "All", value: "" },
  { label: "$1K+", value: "1000" },
  { label: "$5K+", value: "5000" },
  { label: "$10K+", value: "10000" },
  { label: "$25K+", value: "25000" },
  { label: "$50K+", value: "50000" },
];

const AGE_OPTIONS = [
  { label: "All", value: "" },
  { label: "0-1d", value: "0-1" },
  { label: "1-3d", value: "1-3" },
  { label: "3-7d", value: "3-7" },
  { label: "7d+", value: "7-9999" },
];

const STATUS_OPTIONS = [
  { label: "All", value: "" },
  { label: "Wins", value: "win" },
  { label: "Losses", value: "loss" },
  { label: "Pending", value: "pending" },
];

const SIDE_OPTIONS = [
  { label: "All", value: "" },
  { label: "BUY", value: "buy" },
  { label: "SELL", value: "sell" },
];

function FilterGroup({
  label,
  param,
  options,
  current,
  onSelect,
}: {
  label: string;
  param: string;
  options: { label: string; value: string }[];
  current: string;
  onSelect: (param: string, value: string) => void;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-[10px] text-zinc-500 uppercase tracking-wider min-w-[3rem]">
        {label}
      </span>
      <div className="flex gap-0.5">
        {options.map((opt) => {
          const active = current === opt.value;
          return (
            <button
              key={opt.value}
              onClick={() => onSelect(param, opt.value)}
              className={`px-2 py-0.5 text-xs rounded font-mono transition-colors ${
                active
                  ? "bg-zinc-700 text-zinc-100"
                  : "text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800"
              }`}
            >
              {opt.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export default function Filters() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const handleSelect = useCallback(
    (param: string, value: string) => {
      const params = new URLSearchParams(searchParams.toString());
      if (value) {
        params.set(param, value);
      } else {
        params.delete(param);
      }
      const qs = params.toString();
      router.push(qs ? `${pathname}?${qs}` : pathname);
    },
    [router, pathname, searchParams],
  );

  const hasFilters = searchParams.toString().length > 0;

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-3">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-xs font-medium text-zinc-400">Filters</h2>
        {hasFilters && (
          <button
            onClick={() => router.push(pathname)}
            className="text-[10px] text-zinc-500 hover:text-zinc-300 transition-colors"
          >
            Clear all
          </button>
        )}
      </div>
      <div className="flex flex-wrap gap-x-6 gap-y-2">
        <FilterGroup
          label="Score"
          param="score"
          options={SCORE_OPTIONS}
          current={searchParams.get("score") || ""}
          onSelect={handleSelect}
        />
        <FilterGroup
          label="Size"
          param="size"
          options={SIZE_OPTIONS}
          current={searchParams.get("size") || ""}
          onSelect={handleSelect}
        />
        <FilterGroup
          label="Age"
          param="age"
          options={AGE_OPTIONS}
          current={searchParams.get("age") || ""}
          onSelect={handleSelect}
        />
        <FilterGroup
          label="Status"
          param="status"
          options={STATUS_OPTIONS}
          current={searchParams.get("status") || ""}
          onSelect={handleSelect}
        />
        <FilterGroup
          label="Side"
          param="side"
          options={SIDE_OPTIONS}
          current={searchParams.get("side") || ""}
          onSelect={handleSelect}
        />
      </div>
    </div>
  );
}
