import { NavLink, Outlet } from "react-router-dom";
import { motion } from "framer-motion";
import {
  Activity,
  Bot,
  ClipboardList,
  LayoutDashboard,
  Shield,
} from "lucide-react";

const links = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/monitoring", label: "Monitoring", icon: Activity },
  { to: "/auditing", label: "Auditing", icon: Shield },
  { to: "/approvals", label: "Approvals", icon: ClipboardList },
  { to: "/agents", label: "AI Agents", icon: Bot },
];

export function Layout() {
  return (
    <div className="flex min-h-screen">
      <aside className="fixed inset-y-0 left-0 z-20 flex w-64 flex-col border-r border-slate-800/80 bg-surface-900/90 backdrop-blur-xl">
        <motion.div
          initial={{ opacity: 0, x: -12 }}
          animate={{ opacity: 1, x: 0 }}
          className="border-b border-slate-800/80 px-6 py-6"
        >
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-sky-400">
            Telecom
          </p>
          <h1 className="mt-1 text-xl font-bold text-white">Ops Console</h1>
        </motion.div>
        <nav className="flex-1 space-y-1 p-4">
          {links.map((link, i) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.to === "/"}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-xl px-4 py-3 text-sm font-medium transition-all ${
                  isActive
                    ? "bg-sky-500/15 text-sky-300 shadow-inner shadow-sky-500/10"
                    : "text-slate-400 hover:bg-slate-800/50 hover:text-white"
                }`
              }
            >
              <motion.span
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: i * 0.05 }}
              >
                <link.icon className="h-4 w-4" />
              </motion.span>
              {link.label}
            </NavLink>
          ))}
        </nav>
        <p className="border-t border-slate-800/80 p-4 text-xs text-slate-500">
          MCP tools for Cursor, Claude, and any HTTP agent
        </p>
      </aside>
      <main className="ml-64 flex-1 p-8">
        <Outlet />
      </main>
    </div>
  );
}
