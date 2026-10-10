"use client";

import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Bell, LogIn, LogOut, Menu, Moon, Plus, Search, Sun, X } from "lucide-react";
import { useTheme } from "next-themes";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useApprovals, useUI } from "@/lib/store/app";
import { useSession } from "@/lib/store/auth";
import { SidebarLinks } from "./Sidebar";
import { Avatar } from "./badges";

export function TopBar() {
  const { setCommandOpen } = useUI();
  const pending = useApprovals((s) => s.approvals.filter((a) => a.status === "pending").length);
  const { theme, setTheme } = useTheme();
  const [menuOpen, setMenuOpen] = useState(false);
  const { status, user, logout } = useSession();
  const router = useRouter();

  return (
    <>
      <header className="flex h-[60px] shrink-0 items-center gap-2 border-b border-mist-200 bg-white/80 px-3 backdrop-blur sm:gap-3 sm:px-4 lg:px-6 dark:border-white/10 dark:bg-night-900/80">
        <button
          onClick={() => setMenuOpen(true)}
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] border border-mist-200 text-ink-500 lg:hidden dark:border-white/10 dark:text-slate-300"
          aria-label="Open navigation"
        >
          <Menu className="h-4 w-4" />
        </button>
        <button
          onClick={() => setCommandOpen(true)}
          className="flex h-9 min-w-0 max-w-md flex-1 items-center gap-2 rounded-[10px] border border-mist-200 bg-mist-50 px-3 text-[13px] text-ink-400 transition-colors hover:border-brand-200 hover:bg-brand-50 dark:border-white/10 dark:bg-white/5 dark:hover:border-white/20"
        >
          <Search className="h-4 w-4 shrink-0" />
          <span className="flex-1 truncate text-left">What do you want to accomplish?</span>
          <kbd className="hidden rounded border border-mist-200 bg-white px-1.5 py-0.5 text-[10px] font-semibold text-ink-500 sm:block dark:border-white/10 dark:bg-white/5">
            Ctrl K
          </kbd>
        </button>
        <div className="ml-auto flex shrink-0 items-center gap-2">
          <button
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            className="flex h-9 w-9 items-center justify-center rounded-[10px] border border-mist-200 text-ink-500 hover:bg-mist-100 dark:border-white/10 dark:text-slate-300 dark:hover:bg-white/5"
            aria-label="Toggle theme"
          >
            <Sun className="hidden h-4 w-4 dark:block" />
            <Moon className="h-4 w-4 dark:hidden" />
          </button>
          <Link
            href="/approvals"
            className="relative flex h-9 w-9 items-center justify-center rounded-[10px] border border-mist-200 text-ink-500 hover:bg-mist-100 dark:border-white/10 dark:text-slate-300 dark:hover:bg-white/5"
            aria-label="Approvals"
          >
            <Bell className="h-4 w-4" />
            {pending > 0 && (
              <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-bold text-white">
                {pending}
              </span>
            )}
          </Link>
          <Link
            href="/projects/new"
            className="hidden h-9 items-center gap-1.5 rounded-[10px] bg-gradient-to-r from-brand-500 to-sky-400 px-3.5 text-[13px] font-semibold text-white shadow-card transition-opacity hover:opacity-90 sm:flex"
          >
            <Plus className="h-4 w-4" /> New Project
          </Link>
          <Avatar name="Alex Morgan" hue={217} />
          {status === "authenticated" && user ? (
            <button
              onClick={async () => {
                await logout();
                router.push("/login");
              }}
              title={`Log out ${user.email}`}
              aria-label="Log out"
              className="flex h-9 w-9 items-center justify-center rounded-[10px] border border-mist-200 text-ink-500 hover:bg-mist-100 dark:border-white/10 dark:text-slate-300 dark:hover:bg-white/5"
            >
              <LogOut className="h-4 w-4" />
            </button>
          ) : (
            status !== "unknown" && (
              <Link
                href="/login"
                aria-label="Log in"
                className="flex h-9 w-9 items-center justify-center rounded-[10px] border border-mist-200 text-ink-500 hover:bg-mist-100 dark:border-white/10 dark:text-slate-300 dark:hover:bg-white/5"
              >
                <LogIn className="h-4 w-4" />
              </Link>
            )
          )}
        </div>
      </header>

      <AnimatePresence>
        {menuOpen && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 bg-night-950/50 lg:hidden"
            onClick={() => setMenuOpen(false)}
          >
            <motion.div
              initial={{ x: -280 }}
              animate={{ x: 0 }}
              exit={{ x: -280 }}
              transition={{ type: "tween", duration: 0.2 }}
              onClick={(e) => e.stopPropagation()}
              className="flex h-full w-[260px] flex-col gap-4 overflow-y-auto bg-white px-3 py-4 dark:bg-night-900"
            >
              <div className="flex items-center justify-between px-2">
                <span className="text-[13px] font-bold tracking-wide">MUSE SPARK</span>
                <button onClick={() => setMenuOpen(false)} aria-label="Close navigation">
                  <X className="h-4 w-4 text-ink-500" />
                </button>
              </div>
              <div onClick={() => setMenuOpen(false)}>
                <SidebarLinks />
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
