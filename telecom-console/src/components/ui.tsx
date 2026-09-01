import { motion } from "framer-motion";
import type { ReactNode } from "react";

type Props = {
  label: string;
  value: string | number;
  hint?: string;
  tone?: "default" | "success" | "warning" | "danger";
  delay?: number;
};

const toneMap = {
  default: "from-sky-500/20 to-cyan-500/5 border-sky-500/30",
  success: "from-emerald-500/20 to-emerald-500/5 border-emerald-500/30",
  warning: "from-amber-500/20 to-amber-500/5 border-amber-500/30",
  danger: "from-rose-500/20 to-rose-500/5 border-rose-500/30",
};

export function StatCard({ label, value, hint, tone = "default", delay = 0 }: Props) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, delay, ease: [0.22, 1, 0.36, 1] }}
      className={`rounded-2xl border bg-gradient-to-br p-5 backdrop-blur-sm ${toneMap[tone]}`}
    >
      <p className="text-xs font-medium uppercase tracking-wider text-slate-400">{label}</p>
      <p className="mt-2 font-mono text-2xl font-semibold text-white">{value}</p>
      {hint ? <p className="mt-1 text-sm text-slate-400">{hint}</p> : null}
    </motion.div>
  );
}

export function Panel({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <motion.section
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      className="rounded-2xl border border-slate-700/60 bg-surface-900/80 p-6 shadow-xl shadow-black/20 backdrop-blur"
    >
      <div className="mb-4 flex items-center justify-between gap-4">
        <h2 className="text-lg font-semibold text-white">{title}</h2>
        {action}
      </div>
      {children}
    </motion.section>
  );
}

export function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span
      className={`inline-block h-2.5 w-2.5 rounded-full ${ok ? "bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.8)]" : "bg-rose-400"}`}
    />
  );
}
